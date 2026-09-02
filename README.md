# AuEye — 浙商金价监控

一款轻量级 Windows 桌面金价实时监控工具，从京东金融平台抓取浙商银行财富金价格（元/克），以悬浮卡片 + 系统托盘数字显示，支持阈值提醒、邮件通知和异动检测。

## 功能特性

- **实时金价** — 每2秒刷新浙商银行财富金价格（数据源：京东金融黄金平台）
- **托盘大字显示** — 系统托盘图标直接显示实时金价数字（两行大字，涨红跌绿）
- **悬浮卡片** — 可拖拽的透明悬浮窗，显示金价 + 涨跌箭头（右键托盘可切换显示）
- **阈值提醒** — 金价上破/下破设定值时，托盘通知 + 提示音 + 邮件提醒；价格停留阈值区间时按间隔重复发邮件
- **异动检测** — 价格在短时间窗口内剧烈波动时，卡片闪烁 + 蜂鸣提示
- **邮件通知** — 支持自定义 SMTP 服务器（163/Gmail/QQ 等均可），可配置提醒间隔
- **自定义图标** — EXE 软件图标使用自定义设计
- **开机运行** — 双击 exe 即可运行，无依赖

## 快速开始

### 直接运行

```
双击 aueye.exe
```

- 启动后金价数字显示在**系统托盘**（任务栏右下角）
- **右键托盘图标** → 显示/隐藏卡片、设置、退出

### 源码运行

```bash
# 安装依赖
pip install requests pillow pystray

# 运行
python aueye.py
```

## 配置说明

首次运行会在 exe 旁边自动生成 `config.json`，也可通过右键托盘 → **设置** 修改：

| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| 刷新间隔 | 2秒 | 价格抓取频率 |
| 透明度 | 0.55 | 悬浮卡片透明度 |
| 声音提醒 | 开启 | 阈值/异动时播放提示音 |
| 上破提醒 | 关闭 | 金价上穿此值时通知 |
| 下破提醒 | 900.0 | 金价下穿此值时通知 |
| 邮件提醒间隔 | 10分钟 | 价格停留阈值区间时重复发邮件的间隔（≥5分钟） |
| 日志/历史 | 关闭 | 启用后生成 `aueye.log` 和 `aueye_history.csv` |

## 打包为 EXE

```bash
pip install pyinstaller

pyinstaller --onefile --noconsole --strip --name aueye --icon=gold_icon.ico aueye.py \
  --exclude-module numpy --exclude-module tkinter.test \
  --exclude-module setuptools --exclude-module distutils \
  --exclude-module pip --exclude-module pkg_resources
```

产物在 `dist/aueye.exe`（约 20MB）。

## 技术栈

- Python 3.13
- tkinter — 悬浮卡片 UI
- Pillow — 托盘图标渲染
- pystray — 系统托盘
- requests — 京东金融 API 调用

## 项目结构

```
aueye/
├── aueye.py           # 主程序（浙商金价监控）
├── test_aueye.py      # 单元测试
├── gold_icon.ico     # 自定义 EXE 图标
└── CLAUDE.md         # Claude Code 开发文档（不上传）
```

## 数据源

| 数据 | 接口 | 频率 |
|------|------|------|
| 浙商银行财富金 | 京东金融 `cfGetLatestPriceInfo` (productSku=1961543816) | 2秒 |
| Au99.99 7天日线 | 东方财富 `push2his.eastmoney.com`（备用，用于走势图） | 10分钟 |

## License

MIT
