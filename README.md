# astrbot_plugin_klbq

卡拉彼丘（Strinova）战绩查询 AstrBot 插件：按游戏昵称从 [dakanu.com](https://dakanu.com) 取数，
渲染成一张长图（排位段位 / 总览 / 模式 / 角色 / 最近对局）并发送到聊天。

移植自 DSH 插件 [dsh-klbq](https://github.com/CSAHuan/dsh-klbq)（MIT，作者 CSAHuan）。

## 用法

```
/klbq <游戏昵称或UID>
/卡拉彼丘战绩 <游戏昵称或UID>
```

返回：数据摘要文本 + 战绩长图。

昵称匹配规则（v0.1.1 起）：

- **完全同名**才直接查询；只有 1 个相似候选时会自动匹配并提示；
- 有**多个相似昵称**时不会瞎猜（避免「汐汐不汐汐」查成「汐汐不汐汐丿」），
  而是列出候选（含 UID），请用完整昵称或 UID 再查一次。

## 安装

1. 把 `astrbot_plugin_klbq` 整个目录解压到 AstrBot 的 `data/plugins/` 下；
2. 重启 AstrBot（或在 WebUI 插件管理里重载）。

**零第三方依赖**（纯标准库），但渲染长图需要系统里有 Edge / Chrome / Chromium：

- Windows 预装 Edge，开箱即用；
- Linux 服务器请安装 chromium，或用环境变量 `KLBQ_BROWSER` 指定浏览器可执行文件路径。

## 实现要点（改代码前务必先读）

1. **SNI 阻断**：直连 `https://dakanu.com` 会在 TLS 握手阶段被 RST
   （ClientHello 带这个域名就被重置；不带 SNI 直连 IP 能拿到正常响应）。
   因此请求统一「连 IP + 不发 SNI（`wrap_socket(server_hostname=None)`）+ Host 头」，
   并关闭证书校验。这条通道只取公开战绩、不传凭据；**不要**用它传输密钥或隐私数据。
2. **两套段位体系**：排位是「至纯物质 / 升格分子 / 虚空原子 / 聚变质子 / 自由中子 /
   束缚电子 / 禁闭夸克 / 无限超弦 / 奇点」（按 `rank.segment` 号段）；
   挑战赛是「青铜…传奇」（按 `gradeScore` 算）。卡片右上角用排位，别混用。
3. **渲染**：两趟无头浏览器截图——先 `--dump-dom` 量出内容高度，再按高度截图，
   避免长图底部留白。图片写到系统临时目录 `klbq-cards/` 下。

## 文件结构

```
astrbot_plugin_klbq/
├── main.py        # 插件入口：/klbq 指令、查询编排、回复消息链
├── dakanu.py      # 数据访问层：IP 直连 + 无 SNI + Host 头；排位段位映射
├── render.py      # HTML 长图模板 + Edge/Chrome 无头截图
├── metadata.yaml  # AstrBot 插件元数据
└── README.md
```

## 许可

MIT（沿用原作）
