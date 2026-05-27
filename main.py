from __future__ import annotations

import asyncio
import html
import re
import shlex
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_mateprice"
PLUGIN_AUTHOR = "DopplerXD"
PLUGIN_DESC = "把群友重量换算为当日猪肉价格对应的黄金克数。"
PLUGIN_VERSION = "1.0.0"
PLUGIN_REPO = "https://github.com/DopplerXD/astrbot_plugin_mateprice"

SET_COMMAND = "设置群友信息"
QUERY_COMMAND = "查询群友价格"
MATE_INFO_KEY_PREFIX = "mate_info"

SGE_DELAYED_QUOTE_URL = "https://www.sge.com.cn/sjzx/yshqbg"
XINFADI_PRICE_URL = "http://www.xinfadi.com.cn/getPriceData.html"
PRICE_TIMEOUT_SECONDS = 10

TWO_PLACES = Decimal("0.01")
HTTP_HEADERS = {
    "Accept": "application/json,text/html,application/xhtml+xml",
    "User-Agent": "AstrBot MatePrice/1.0",
}


class MatePriceError(Exception):
    """Base exception for user-facing plugin errors."""


class CommandUsageError(MatePriceError):
    """Raised when command arguments are invalid."""


class PriceFetchError(MatePriceError):
    """Raised when a price source cannot be fetched or parsed."""


@register(PLUGIN_NAME, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION, PLUGIN_REPO)
class MatePricePlugin(Star):
    """根据群友重量查询对应的猪肉价格和黄金克数。"""

    def __init__(self, context: Context):
        super().__init__(context)

    @filter.command(SET_COMMAND)
    async def set_mate_info(self, event: AstrMessageEvent):
        """Set mate name and weight for the current conversation."""
        try:
            name, weight_kg = self._parse_set_command(event.message_str)
        except CommandUsageError as exc:
            yield event.plain_result(f"{exc}\n{self._usage_text()}")
            return

        await self.put_kv_data(
            self._mate_info_key(event),
            {"name": name, "weight_kg": self._format_fixed(weight_kg)},
        )
        yield event.plain_result(
            f"已设置群友信息：{name}，重量 {self._format_trimmed(weight_kg)} kg",
        )

    @filter.command(QUERY_COMMAND)
    async def query_mate_price(self, event: AstrMessageEvent):
        """Query the current conversation's mate price."""
        mate_info = await self.get_kv_data(self._mate_info_key(event), None)
        if not self._is_valid_mate_info(mate_info):
            yield event.plain_result(
                f"请先使用 /{SET_COMMAND} -name 张三 -w 70 设置群友信息。"
            )
            return

        name = str(mate_info["name"])
        weight_kg = self._parse_positive_decimal(str(mate_info["weight_kg"]))

        try:
            gold_price, pork_price_per_kg = await self._fetch_prices()
        except PriceFetchError as exc:
            logger.warning(f"MatePrice price query failed: {exc}")
            yield event.plain_result(f"行情查询失败：{exc} 请稍后再试。")
            return

        gold_grams = (weight_kg * pork_price_per_kg / gold_price).quantize(
            TWO_PLACES,
            rounding=ROUND_HALF_UP,
        )
        yield event.plain_result(
            "\n".join(
                [
                    f"今日金价：{self._format_fixed(gold_price)} 元/克",
                    f"{name} 相当于 {self._format_fixed(gold_grams)} 克黄金",
                ],
            ),
        )

    def _parse_set_command(self, message: str) -> tuple[str, Decimal]:
        arg_text = self._strip_command_name(message, SET_COMMAND)
        if not arg_text:
            raise CommandUsageError("参数不足。")

        try:
            tokens = shlex.split(arg_text)
        except ValueError as exc:
            raise CommandUsageError("参数解析失败，请检查引号是否闭合。") from exc

        name: str | None = None
        weight_text: str | None = None
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in {"-name", "--name"}:
                index += 1
                if index >= len(tokens):
                    raise CommandUsageError("缺少群友名称。")
                name = tokens[index].strip()
            elif token in {"-w", "--weight"}:
                index += 1
                if index >= len(tokens):
                    raise CommandUsageError("缺少群友重量。")
                weight_text = tokens[index].strip()
            else:
                raise CommandUsageError(f"未知参数：{token}")
            index += 1

        if not name:
            raise CommandUsageError("缺少群友名称。")
        if weight_text is None:
            raise CommandUsageError("缺少群友重量。")

        weight_kg = self._parse_positive_decimal(weight_text)
        return name, weight_kg.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    def _strip_command_name(self, message: str, command_name: str) -> str:
        message = message.strip()
        match = re.match(rf"^/?{re.escape(command_name)}(?:\s+|$)", message)
        if match is None:
            return message
        return message[match.end() :].strip()

    def _usage_text(self) -> str:
        return f"用法：/{SET_COMMAND} -name 张三 -w 70"

    def _mate_info_key(self, event: AstrMessageEvent) -> str:
        return f"{MATE_INFO_KEY_PREFIX}:{event.unified_msg_origin}"

    def _is_valid_mate_info(self, value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        name = value.get("name")
        weight_kg = value.get("weight_kg")
        if not isinstance(name, str) or not name.strip():
            return False
        try:
            self._parse_positive_decimal(str(weight_kg))
        except CommandUsageError:
            return False
        return True

    def _parse_positive_decimal(self, text: str) -> Decimal:
        try:
            value = Decimal(text)
        except (InvalidOperation, ValueError) as exc:
            raise CommandUsageError("重量必须是正数。") from exc
        if not value.is_finite() or value <= 0:
            raise CommandUsageError("重量必须是正数。")
        return value

    async def _fetch_prices(self) -> tuple[Decimal, Decimal]:
        timeout = aiohttp.ClientTimeout(total=PRICE_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(
            headers=HTTP_HEADERS,
            timeout=timeout,
            trust_env=True,
        ) as session:
            gold_price, pork_price_per_kg = await asyncio.gather(
                self._fetch_gold_price(session),
                self._fetch_pork_price_per_kg(session),
            )
        return gold_price, pork_price_per_kg

    async def _fetch_gold_price(self, session: aiohttp.ClientSession) -> Decimal:
        try:
            async with session.get(SGE_DELAYED_QUOTE_URL) as response:
                response.raise_for_status()
                text = await response.text(encoding="utf-8", errors="ignore")
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PriceFetchError("金价接口不可用。") from exc

        return self._parse_sge_gold_price(text)

    async def _fetch_pork_price_per_kg(
        self,
        session: aiohttp.ClientSession,
    ) -> Decimal:
        params = {
            "limit": "20",
            "current": "1",
            "pubDateStartTime": "",
            "pubDateEndTime": "",
            "prodPcatid": "",
            "prodCatid": "",
            "prodName": "白条猪",
        }
        try:
            async with session.get(XINFADI_PRICE_URL, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise PriceFetchError("猪肉价格接口不可用。") from exc

        return self._parse_xinfadi_pork_price(data)

    def _parse_sge_gold_price(self, text: str) -> Decimal:
        row_match = re.search(
            r"<tr[^>]*>.*?<td[^>]*>\s*Au99\.99\s*</td>.*?</tr>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if row_match is None:
            raise PriceFetchError("未找到 Au99.99 金价。")

        cells = re.findall(
            r"<td[^>]*>(.*?)</td>",
            row_match.group(0),
            flags=re.IGNORECASE | re.DOTALL,
        )
        if len(cells) < 2:
            raise PriceFetchError("金价返回格式无法解析。")

        latest_price = self._clean_html_text(cells[1])
        price = self._parse_price_decimal(latest_price, "金价返回格式无法解析。")
        if price <= 0:
            raise PriceFetchError("金价返回值无效。")
        return price

    def _parse_xinfadi_pork_price(self, data: Any) -> Decimal:
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise PriceFetchError("猪肉价格返回格式无法解析。")

        items = [
            item
            for item in data["list"]
            if isinstance(item, dict) and item.get("prodName") == "白条猪"
        ]
        if not items:
            raise PriceFetchError("未找到白条猪价格。")

        latest_pub_date = max(str(item.get("pubDate", "")) for item in items)
        latest_items = [
            item for item in items if str(item.get("pubDate", "")) == latest_pub_date
        ]
        prices = [self._normalize_pork_price_per_kg(item) for item in latest_items]
        prices = [price for price in prices if price > 0]
        if not prices:
            raise PriceFetchError("猪肉价格返回值无效。")
        return sum(prices, Decimal("0")) / Decimal(len(prices))

    def _normalize_pork_price_per_kg(self, item: dict[str, Any]) -> Decimal:
        price = self._parse_price_decimal(
            str(item.get("avgPrice", "")),
            "猪肉价格返回格式无法解析。",
        )
        unit = str(item.get("unitInfo", "")).strip().lower()
        if unit in {"斤", "500g", "500克"}:
            return price * Decimal("2")
        if unit in {"kg", "公斤", "千克"}:
            return price
        raise PriceFetchError(f"无法识别猪肉价格单位：{unit or '空'}。")

    def _clean_html_text(self, value: str) -> str:
        text = re.sub(r"<[^>]+>", "", value)
        return html.unescape(text).strip()

    def _parse_price_decimal(self, text: str, error_message: str) -> Decimal:
        try:
            value = Decimal(text.replace(",", "").strip())
        except (InvalidOperation, ValueError) as exc:
            raise PriceFetchError(error_message) from exc
        if not value.is_finite():
            raise PriceFetchError(error_message)
        return value

    def _format_fixed(self, value: Decimal) -> str:
        return str(value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))

    def _format_trimmed(self, value: Decimal) -> str:
        fixed = self._format_fixed(value)
        return fixed.rstrip("0").rstrip(".")
