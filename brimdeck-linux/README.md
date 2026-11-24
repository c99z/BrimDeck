# BrimDeck for Linux

这是 [BrimDeck](https://github.com/MoFeng2223/BrimDeck)（Windows 上的“灵动岛”）的 Linux 移植版，用 Python 3、GTK 3 和 Cairo 编写，运行在 GNOME / X11 上。
默认收起在系统顶栏中央，位于日期左侧。C、X 两个环分别显示 Claude、Codex 的额度用量，旁边的百分比是 CPU 使用率。单击状态条展开或收起面板，右键打开设置，单击系统日期打开日历。

| 页面 | 内容 | 数据来源 |
|---|---|---|
| AI 用量 | Claude Code、Codex 的额度、重置倒计时，今日和近 7 天的 Token 数与估算费用，每天的柱状图，按模型的明细 | 本地会话日志 `~/.claude/projects`、`~/.codex/sessions`（增量读取）；Codex 额度通过它自己的登录调用 `chatgpt.com/backend-api/wham/usage`，失败时改用会话日志里记录的额度；有 Claude 订阅登录时调用 `api.anthropic.com/api/oauth/usage`；价格取自 OpenRouter 价格表，每天缓存一次 |
| 音乐 | 封面、标题、歌手、进度条（可点击跳转）、播放控制、随机和循环、多个播放器之间切换 | MPRIS（D-Bus） |
| 系统 | CPU（含每个核心）、内存和交换、GPU（使用率、显存、温度、功耗）、网络、磁盘、温度、占用最高的应用 | psutil、`nvidia-smi`、lm-sensors |
| 剪贴板 | 文字和图片的历史，链接、颜色、代码分别显示，可收藏；文件中转站（把文件拖到刘海或面板上暂存，再拖出或点击粘贴） | GTK 剪贴板；粘贴时用 XTest 发送 Ctrl+V，终端里发 Ctrl+Shift+V |
| 设备 | 无线鼠标、键盘、耳机、手柄等设备的电量，以及笔记本电池 | UPower |

## 运行

```bash
./brimdeck.py                 # 直接运行
./install.sh                  # 安装到 ~/.local/bin/brimdeck，并加到应用菜单
./install.sh --autostart      # 同上，并设置登录后自动启动
./install.sh --uninstall
```

依赖：`python3-gi`、`gir1.2-gtk-3.0`、`python3-gi-cairo`、`python3-psutil`、`gir1.2-wnck-3.0`。Ubuntu 24.04 桌面版默认都已装好。

## 操作

- 安装脚本会为 GNOME Shell 46 安装 BrimDeck 扩展。首次安装后重新登录即可加载；在 X11 桌面也可以按 Alt+F2、输入 `r` 并回车刷新顶栏。扩展未加载时自动显示独立刘海。
- 「设置 → 通用 → 面板展开方式」同时控制顶栏状态条和独立刘海。选择「悬停展开」后，指针停在状态条或刘海上会展开面板，移入面板可继续操作，移出后按设定的延迟收起。选择「点击展开」后，单击即可切换。顶栏状态条右键打开设置；切换到独立刘海时，右键打开菜单，中键收起面板。
- 在面板顶部的标签栏上滚动鼠标滚轮可以切换页面。
- 命令行（只会有一个实例在运行，后面的命令都发给它）：
  - `brimdeck --toggle`：展开或收起面板
  - `brimdeck --page music`：打开指定页面，可选 `usage`、`music`、`monitor`、`clipboard`、`devices`
  - `brimdeck --settings`：打开设置
  - `brimdeck --quit`：退出
- 绑定全局快捷键：在 GNOME 设置 › 键盘 › 查看及自定义快捷键 › 自定义快捷键 中，把命令设为 `brimdeck --toggle`。用快捷键打开的面板会接收键盘输入：Esc 关闭，←/→ 或 Tab 切换页面，数字键直接跳到对应页面，点击别处也会收起。
- 有全屏窗口时自动隐藏。

## 设置与数据

- 设置文件：`~/.config/brimdeck/config.json`（也可以在设置窗口里修改）
- 剪贴板历史和文件中转站列表：`~/.local/share/brimdeck/`。剪贴板历史默认在退出时清除，收藏的条目会保留。
- 价格和封面缓存：`~/.cache/brimdeck/`
- ChatAPI 个人令牌保存在 `~/.config/brimdeck/chatapi.token`（纯文本单行，权限 `0600`，遵循 `XDG_CONFIG_HOME`）。配置后，「AI 用量 → ChatAPI 额度」会列出已启用额度池的余额、用量、时段和北京时间的重置时间。「本机用量」可查看原有统计。更换令牌后点击面板右上角刷新即可，也可等待自动刷新（默认 5 分钟）。
- Claude 和 Codex 的登录令牌从各自工具读取，仅在内存中使用。

## 和 Windows 版的区别

- 支持 Claude、Codex 和 ChatAPI 个人额度。
- 均衡器是装饰性动画，不是对系统音频做的实时频谱分析。
- 没有歌词，也没有为网易云、QQ 音乐做专门适配，只要是 MPRIS 播放器都支持。
- 设备电量依赖 UPower，没有移植 Windows 版里各品牌的私有 HID 协议。
- 不支持主题编辑、胶囊和指示条这两种折叠样式、剪贴板局域网同步、自动更新。
- 需要 X11 才能做到鼠标穿透和窗口置顶。在 Wayland 会话里会通过 XWayland 运行，但 GNOME Wayland 下置顶不一定可靠。
