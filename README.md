# KeyHarbor

**通过增加额外模型选项，将第三方 API 接入 Codex，同时保留官方模型与原生回退。**

Add third-party APIs to Codex as additional model options, while preserving official models and a native fallback path.

KeyHarbor 是运行在本机的第三方模型接入与管理层。它动态读取官方目录，再按用户配置追加额外模型，而不是用某家供应商替换整个官方模型目录。隐藏模型仍可通过精确别名调用。

项目基于 LiteLLM 的协议适配能力构建，运行独立 LiteLLM 子进程，不修改其安装包。KeyHarbor 是独立项目，不是 OpenAI、Codex 或 LiteLLM 官方产品。

## 功能

- 增量模型目录：保留官方元数据，只追加明确设为可见的第三方模型。
- 多接入管理：自定义显示名、稳定 ID、模型前缀、启停和秘钥轮换，不限定两份接入或特定供应商。
- 动态发现：从各自上游读取模型，配置变化约每 5 秒检查，上游默认每 300 秒刷新，也可手动刷新。
- 本机控制台：秘钥导入、状态、逐模型可见性、备用关系、刷新与模式切换。
- 热更新：候选配置验证后切换，旧请求完成后退出旧进程；失败保留旧配置。
- 受控备用：只切换到明确配置的相同模型/协议，不跨秘钥重放已有会话或已输出的流。
- 原生回退：独立命令和守护进程可撤销网关配置，不依赖推理网关存活。

## 支持范围

首版面向 **Windows / Python 3.14**，凭据通过 Windows CurrentUser DPAPI 加密。支持 OpenAI-compatible Chat Completions / Responses 文本接口，不代表任意上游协议或所有模型能力均可用。

需要已有可用的 Codex 官方登录态，以及有权使用的第三方 API。模型的工具调用、上下文、搜索等能力仍需分别验证；目录出现模型不是完整能力认证。

## 快速开始

在项目目录的 PowerShell 中：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item registry.example.json registry.json
.\tools\start.ps1 -WithWatchdog -Open
```

已有 `registry.json` 时不要覆盖。管理页面为 [http://127.0.0.1:4104/](http://127.0.0.1:4104/)，网关默认位于 `http://127.0.0.1:4103/v1`。

1. 导入接入：输入显示名称、唯一 ID、前缀、HTTPS API 地址和秘钥。
2. 自动发现模型；上游无协议元数据时，可显式选择 Chat Completions 或 Responses。
3. 勾选要在 Codex 中展示的模型。新接入默认隐藏。
4. 验证就绪后点击“使用管理网关”，或运行下方命令。

```powershell
.\tools\keyharbor.ps1 status
.\tools\keyharbor.ps1 managed
.\tools\keyharbor.ps1 reload
.\tools\keyharbor.ps1 native

# 本机隐蔽输入；不要在命令参数或聊天里传秘钥
.\tools\keyharbor.ps1 key-set supplier-a --replace
.\tools\keyharbor.ps1 key-reset supplier-a
```

也可使用 `python -m keyharbor`。旧实现包名、入口和线协议标识暂时保留兼容，避免破坏已有本机部署；它们不是供应商限制。

启动器默认使用项目 `.venv`，可用 `KEYHARBOR_PYTHON` 或不提交 Git 的 `tools/runtime.local.json` 指定 Python。验证工具支持 `--codex-cli` 指定 Codex 可执行文件。

## 安全与运行边界

- 仅监听 loopback，管理操作检查同源与 CSRF。仅适用于受信任的单用户本机环境，不面向公网或多用户管理。
- 官方身份凭据不转发给第三方上游；各私有接入使用自己的秘钥。保存后不回显。
- 官方目录需要官方登录凭据；401 不伪装成空目录，也不能直接据此断言推理失效。
- 上游协议未知时不猜测，Anthropic 专用和图像接口不自动当成 Codex 文本模型。
- 401、明确额度耗尽和限流分别隔离或暂退；不是所有 403/404 都表示整把秘钥失效。超时、5xx、已开始的流不盲目重放。
- 会话绑定保留在内存中，最长 24 小时、最多 20,000 条；重启或轮换后不安全的续接会明确拒绝。
- watchdog 连续三次（间隔 5 秒）发现服务不可用，或全部启用秘钥被确认为失效/耗尽时，可切回原生配置；不自动切回来，也不重启 Codex。
- **已有 Codex Desktop 可能缓存地址或目录。配置切换不等于旧会话即时切换；必要的桌面重启由用户决定。**
- 原生可用仍依赖官方登录、额度和网络。显式指定其他地址的外部程序不会自动迁移。
- 首版不注册开机服务；Windows 重启后需手动启动，或执行 `native` 恢复原生。
- 配置切换先做定点备份。通过 `--backup-root`、`KEYHARBOR_BACKUP_ROOT` 或本机 registry 的 `backup_root` 设置位置；默认在用户目录下 `.keyharbor/backups`。
- 移除接入只移除配置，旧 DPAPI 文件保留用于恢复，不能直接跨 Windows 用户迁移。
- 摘要 checkpoint 是编码而非加密，不能当作敏感资料的加密存储。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 需要当前本机登录态及运行中的网关；推理会产生正常 API 用量
.\.venv\Scripts\python.exe tools/verify_live.py
.\.venv\Scripts\python.exe tools/verify_live.py --inference --model supplier-a-model-name
.\.venv\Scripts\python.exe tools/verify_catalog_appserver.py
.\.venv\Scripts\python.exe tools/verify_native.py --model YOUR_OFFICIAL_MODEL
```

自动测试使用合成接入和本机模拟上游，不读取真实用户秘钥。真实验证器只输出状态与模型标识，不打印凭据或响应正文。自动测试、目录读回、真实调用和桌面视觉验收是不同层级，不能互相替代。

## 公开仓库约定

**不提交任何真实秘钥，包括加密后的凭据文件。** 真实 `registry.json`、DPAPI 文件、运行目录、环境变量文件、机器配置、本地备份及验收记录均不发布。示例配置为空，不包含真实接入。

请勿上传 Codex 登录态、会话历史、个人配置或故障日志。新功能采用开发分支和 PR 评审；本仓库 PR 不等于向 LiteLLM 上游贡献。依赖保留各自的许可证与权利声明。
