# astrbot_plugin_mateprice

群友价格查询插件。插件按会话保存群友名称和体重，查询时实时获取上海黄金交易所 `Au99.99` 延时行情和北京新发地 `白条猪` 批发价，把群友体重对应的猪肉价格换算为黄金克数。

## 功能

- `/设置群友信息`：保存当前群聊或私聊的群友名称和体重。
- `/查询群友价格`：实时查询金价和猪肉价，并输出群友对应的黄金克数。
- 群友资料按 AstrBot 会话隔离保存，不同群聊不会互相覆盖。
- 不需要额外安装依赖，插件使用 AstrBot 已依赖的 `aiohttp`。

## 安装

将插件目录放到 AstrBot 的插件目录：

```text
data/plugins/astrbot_plugin_mateprice
```

重启 AstrBot，或在 WebUI 中重载插件。

## 指令

### 设置群友信息

```text
/设置群友信息 -name 张三 -w 70
```

参数：

- `-name` 或 `--name`：群友名称。名称包含空格时请使用引号，例如 `-name "张 三"`。
- `-w` 或 `--weight`：体重，单位为千克。输入允许任意小数，保存时四舍五入保留两位。

示例：

```text
/设置群友信息 -name 张三 -w 70.555
```

保存后重量为 `70.56 kg`。

### 查询群友价格

```text
/查询群友价格
```

输出格式：

```text
今日金价：985.20 元/克
张三 相当于 0.90 克黄金
```

## 计算方式

```text
黄金克数 = 体重(kg) * 猪肉单价(元/kg) / 金价(元/g)
```

价格来源：

- 金价：上海黄金交易所延时行情页面中的 `Au99.99` 最新价，单位按 `元/克` 处理。
- 猪肉价：北京新发地 `白条猪` 价格接口中的 `avgPrice`。接口返回单位为 `斤` 时，插件会乘以 `2` 换算为 `元/kg`；同一最新日期存在多个规格时，取这些规格 `avgPrice` 的算术平均值。

## 异常处理

- 未设置群友信息时，查询会提示先执行 `/设置群友信息`。
- 参数缺失、重量不是正数、引号未闭合时，会返回用法提示。
- 行情接口不可用或返回结构变化时，会提示行情查询失败，不会中断 AstrBot。

## 开发调试

在 AstrBot 仓库根目录运行：

```bash
uv run python -m compileall data/plugins/astrbot_plugin_mateprice/main.py
uv run ruff format .
uv run ruff check .
```

## 注意

- 上海黄金交易所页面为延时行情，结果不代表实时成交价格。
- 北京新发地猪肉价格是北京批发市场参考价，不代表所有地区零售价。
- 两个数据源都是免密接口或页面，若对方调整字段、路径或访问策略，插件需要同步更新解析逻辑。

## 参考

- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [插件最小实例](https://docs.astrbot.app/dev/star/guides/simple.html)
- [插件数据存储](https://docs.astrbot.app/dev/star/guides/storage.html)
