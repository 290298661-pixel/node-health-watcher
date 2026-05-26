<p align="center">
  <h1 align="center">🏥 Node Health Watcher</h1>
  <p align="center"><strong>Kubernetes 节点定时巡检与 IM 告警中心 / Scheduled K8s Node Health Inspection & Instant-Messaging Alerting</strong></p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License">
  <img src="https://img.shields.io/badge/platform-linux%2Famd64-lightgrey" alt="Linux/amd64">
  <img src="https://github.com/noneedtostudy/node-health-watcher/actions/workflows/ci.yml/badge.svg" alt="CI">
</p>

---

## 目录 / Table of Contents

- [概述](#概述)
- [快速开始](#快速开始)
- [检查项](#检查项)
- [架构](#架构)
- [配置说明](#配置说明)
- [开发](#开发)
- [贡献](#贡献)
- [许可证](#许可证)
- [English](#english)

---

## 概述

**Node Health Watcher** 是一款面向 K8s 集群节点的定时巡检与即时通信告警工具。它以中心化调度、SSH 远程巡检、分级告警与去重抑制为核心，让运维工程师无需手动登录每一台节点即可感知集群健康状态。

### 为什么需要 Node Health Watcher？

[node-guardian](https://github.com/noneedtostudy/node-guardian) 解决了"排查怎么做"——当你 SSH 进一台故障节点时，它有成套的诊断、加固与安全审计工具。但它解决不了"排查什么时候做"——你无法每天凌晨三点手动 SSH 进 50 个节点逐台检查。

**Node Health Watcher 填补了这个空白：** 它不需要你主动登录任何节点。定时调度 → SSH 巡检 → 比对阈值 → 有异常推飞书/钉钉，没异常保持静默。你只在需要处理问题时收到消息。

### 核心原则

| 原则 | 实现方式 |
|------|---------|
| **中心化调度** | 单点部署，APScheduler 驱动定时任务，支持 cron 表达式，无需在节点上安装 agent |
| **分级告警** | 每个检查项定义 WARNING / CRITICAL 两级阈值，不同级别可路由至不同 IM 渠道 |
| **去重抑制** | 有状态告警去重：同一节点同一告警项只发首条，恢复后发恢复通知，避免重复刷屏 |
| **可扩展检查** | 基于抽象基类的检查插件体系，添加新检查项无需修改调度器和告警逻辑 |
| **防御性执行** | SSH 超时、认证失败、节点不可达等异常全链路捕获，单节点失败不影响其余节点巡检 |

---

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/noneedtostudy/node-health-watcher.git
cd node-health-watcher

# 安装依赖
pip install -e .

# 初始化配置文件
cp config/nodes.example.yaml config/nodes.yaml
cp config/thresholds.example.yaml config/thresholds.yaml
cp config/alerting.example.yaml config/alerting.yaml

# 按实际情况编辑节点清单与告警配置
vim config/nodes.yaml
vim config/alerting.yaml

# 干运行 — 立即执行一轮巡检，只输出结果，不发送告警
python -m node_health_watcher --dry-run

# 启动定时调度器（默认每 5 分钟巡检一次）
python -m node_health_watcher --interval 5m

# 单次巡检并发送告警
python -m node_health_watcher --once

# 指定自定义 cron 表达式
python -m node_health_watcher --cron "*/10 * * * *"
```

### 环境要求

- **Python 3.10+**
- **控制节点（部署本工具的主机）** 需能够 SSH 免密登录所有目标 K8s 节点
- **目标节点** 需运行 Linux（内核 4.x+），无需安装任何额外组件
- **可选依赖：** `cryptography`（Ed25519 密钥支持）、`rich`（终端彩色输出）
- **journald 持久化：** 国内云服务器默认镜像常关闭 journald 持久化存储以节省磁盘。建议目标节点启用 `Storage=persistent`（`/etc/systemd/journald.conf`），否则 kubelet 日志扫描（PLEG 延迟、错误日志）将自动回退至读取 `/var/log/kubelet.log`

---

## 检查项

每项检查包含若干子项，每个子项独立判定、独立告警。所有检查通过 SSH 在目标节点上只读执行，不会修改任何系统状态。

### disk — 磁盘检查

```
检查对象: 磁盘空间、inode、关键挂载点、I/O 延迟
```

**子检查项：**
1. **空间使用率** — 遍历指定挂载点（默认 `/`、`/var/lib/kubelet`、`/var/lib/containerd`），超过阈值告警
2. **inode 使用率** — 文件数耗尽比空间耗尽更难排查，独立检测 inode 占用
3. **只读文件系统** — 检测关键挂载点是否因异常被 remount 为只读
4. **磁盘 I/O 延迟**（可选）— 通过 `iostat` 检测磁盘平均等待时间

**输出示例：**
```
[2026-05-24 10:15:32] [OK] [node-1] disk: / = 62% (阈值: 80%/90%)
[2026-05-24 10:15:32] [WARN] [node-1] disk: /var/lib/kubelet = 86% (阈值: 80%/90%)
[2026-05-24 10:15:32] [OK] [node-1] disk: inode / = 34% (阈值: 80%/90%)
[2026-05-24 10:15:32] [OK] [node-1] disk: /var/lib/kubelet 读写正常
```

### memory — 内存检查

```
检查对象: 可用内存、Swap、OOM 事件
```

**子检查项：**
1. **可用内存比例** — `MemAvailable` 低于阈值告警（比 `MemFree` 更准确反映可用量）
2. **Swap 使用率** — K8s 节点通常应禁用 swap 或保持极低使用，swap 活动预示内存压力
3. **近期 OOM 事件** — 在 `journalctl` / `dmesg` 中检索指定时间窗口内的 OOM Kill 记录
4. **内存压力 Top-N 进程**（可选）— 输出内存占用最高的 N 个进程及对应 Pod

**输出示例：**
```
[2026-05-24 10:15:32] [OK] [node-2] memory: MemAvailable = 45% (阈值: 20%/10%)
[2026-05-24 10:15:32] [OK] [node-2] memory: Swap = 0% (阈值: 10%/30%)
[2026-05-24 10:15:32] [CRIT] [node-2] memory: 过去 15 分钟内检测到 3 次 OOM Kill
```

### conntrack — 连接跟踪检查

```
检查对象: conntrack 表使用率、连接统计
```

**子检查项：**
1. **表使用率（两级告警）** — ≥85% WARNING / ≥95% CRITICAL，按节点 `conntrack_max` 计算实际占比
2. **表溢出丢包** — 通过 `/proc/sys/net/netfilter/nf_conntrack_count` 与 `nf_conntrack_max` 对比，同时检查 `nf_conntrack_drop` 计数器
3. **TIME_WAIT 连接堆积** — 高并发短连接场景下 TIME_WAIT 堆积先于 conntrack 耗尽出现

**输出示例：**
```
[2026-05-24 10:15:32] [OK] [node-3] conntrack: 表使用率 = 23% (阈值: 85%/95%)
[2026-05-24 10:15:32] [OK] [node-3] conntrack: nf_conntrack_drop = 0
[2026-05-24 10:15:32] [WARN] [node-3] conntrack: TIME_WAIT 连接数 = 12438 (阈值: 10000)
```

### kubelet — Kubelet 健康检查

```
检查对象: kubelet 服务状态、节点状态、关键日志
```

**子检查项：**
1. **服务运行状态** — `systemctl is-active kubelet`，非 active 直接告警
2. **节点 Ready 状态** — 通过 `kubectl` 检查节点是否 Ready（节点名优先使用 `k8s_node_name`，回退至 `hostname`）
3. **PLEG 延迟** — 在 kubelet 日志中检索 PLEG (Pod Lifecycle Event Generator) 延迟告警（优先 journalctl，回退至 /var/log/kubelet.log）
4. **近期关键错误** — 在指定时间窗口内过滤 `error|timeout|deadline|backoff|eviction` 模式（同样支持 journald → 文件日志回退）

**输出示例：**
```
[2026-05-24 10:15:32] [OK] [node-1] kubelet: 服务 active (running)
[2026-05-24 10:15:32] [OK] [node-1] kubelet: Node Ready=True
[2026-05-24 10:15:32] [WARN] [node-1] kubelet: 检测到 PLEG 延迟 3.2s (阈值: 2s)
[2026-05-24 10:15:32] [OK] [node-1] kubelet: 过去 15 分钟无关键错误
```

### kernel — 内核异常检查

```
检查对象: 内核日志异常、hung_task、文件系统错误
```

**子检查项：**
1. **dmesg 关键事件** — 检索 `BUG|panic|segfault|WARNING|Hardware Error` 等内核异常
2. **hung_task 检测** — 内核 hung_task 超时事件，通常指向 I/O 阻塞或死锁
3. **EXT4/XFS 错误** — 检索文件系统级 I/O 错误、元数据损坏告警
4. **内核 Oops 计数** — 启动以来的 kernel oops 数量变化

**输出示例：**
```
[2026-05-24 10:15:32] [OK] [node-4] kernel: dmesg 无关键异常
[2026-05-24 10:15:32] [OK] [node-4] kernel: 无 hung_task 事件
[2026-05-24 10:15:32] [CRIT] [node-4] kernel: EXT4-fs error (device sdb1) — 文件系统元数据错误
```

---

## 架构

```
.
├── node_health_watcher/        # 应用主包
│   ├── __init__.py
│   ├── __main__.py             # CLI 入口（argparse）
│   ├── scheduler.py            # APScheduler 编排引擎
│   ├── config.py               # YAML 配置加载与校验
│   ├── checks/                 # 检查插件
│   │   ├── __init__.py
│   │   ├── base.py             # 抽象检查基类（接口 + 结果模型）
│   │   ├── disk.py             # 磁盘检查
│   │   ├── memory.py           # 内存检查
│   │   ├── conntrack.py        # 连接跟踪检查
│   │   ├── kubelet.py          # Kubelet 健康检查
│   │   └── kernel.py           # 内核异常检查
│   ├── transport/              # 远程执行层
│   │   ├── __init__.py
│   │   ├── ssh.py              # paramiko SSH 客户端封装
│   │   └── executor.py         # ThreadPoolExecutor 并发调度
│   └── alert/                  # 告警输出
│       ├── __init__.py
│       ├── common.py           # 飞书/钉钉共享格式工具
│       ├── feishu.py           # 飞书 Webhook 推送
│       ├── dingtalk.py         # 钉钉 Webhook 推送
│       └── dedup.py            # 有状态告警去重与恢复检测
├── config/                     # 配置文件
│   ├── nodes.example.yaml      # 节点清单模板
│   ├── thresholds.example.yaml # 告警阈值模板
│   └── alerting.example.yaml   # IM Webhook 路由模板
├── tests/                      # pytest 单元测试
│   ├── conftest.py             # 共享 fixtures（mock SSH、假节点）
│   ├── test_disk.py
│   ├── test_memory.py
│   ├── test_conntrack.py
│   ├── test_kubelet.py
│   ├── test_kernel.py
│   └── test_dedup.py
├── .github/workflows/
│   └── ci.yml                  # CI: ruff lint + pytest + coverage
├── pyproject.toml
└── README.md
```

### 设计决策

**为什么用 Python 而不是继续用 Bash？**

node-guardian 选 Bash 是因为它跑在目标节点上，零运行时依赖。Node Health Watcher 跑在控制节点上，需要：结构化配置解析（YAML）→ 并发 SSH 多节点 → 结构化解析输出 → 构造 JSON 推送 IM webhook。这条链路每一步都在处理结构化数据，Python 天然适合。而且 APScheduler 的 cron 调度、线程池并发、IM webhook 签名计算，用 Bash 写会迅速失控。

**为什么选 paramiko + ThreadPoolExecutor 而不是 asyncssh？**

节点规模在百台以内时，5-10 个线程的 ThreadPoolExecutor 已经足够——瓶颈在节点命令执行时间而非 SSH 握手。paramiko 是纯 Python 生态中最成熟的选择，文档丰富，对接企业环境（跳板机、代理、PKey）方案完备。如果未来扩展到 500+ 节点，迁移到 asyncssh 的成本可控。

**为什么选 APScheduler 而不是 Linux cron？**

- **进程内调度**：无需依赖系统 crond，单进程即可部署，容器化友好
- **misfire 处理**：任务积压时支持丢弃/合并/立即执行三种策略
- **时区感知**：cron 表达式原生支持时区，不会因 UTC/本地时间搞混
- **动态任务**：支持运行时增删检查任务，无需重启进程

**告警去重机制**

首次检测到异常 → 发告警并记录状态（节点+检查项+子项+级别）。后续巡检同一异常持续存在 → 抑制（不重复发送），仅在巡检日志中记录。异常恢复 → 发恢复通知，清除记录。

状态存储默认使用内存 dict（轻量、零依赖）；可通过 `--state-file` 指定 JSON 文件路径实现进程重启后保留状态。

```json
{
  "node-1": {
    "disk:/var/lib/kubelet": {"level": "WARNING", "since": "2026-05-24T10:15:32"},
    "memory:oom": {"level": "CRITICAL", "since": "2026-05-24T10:10:00"}
  }
}
```

**为什么每个检查项都有多个子检查？**

单一指标只能告诉你"磁盘满了"，但不告诉你"为什么满"、"是不是刚满"、"是不是 inode 耗尽而非空间耗尽"。多维子检查组合才能给出可行动的告警。以 disk 为例：空间 90% + inode 35% → 单一大文件写入，空间 62% + inode 92% → 海量小文件，排查方向完全不同。

**干运行模式**

`--dry-run` 执行完整的巡检流程（SSH 连接 → 命令执行 → 结果解析），但跳过告警推送。适用于：上线前验证配置是否正确、调试阈值设定、验证 SSH 连通性。干运行输出与正常模式完全一致，日志中标注 `[DRY-RUN]`。

---

## 配置说明

### 节点清单 (`config/nodes.yaml`)

```yaml
# 节点列表，支持按组分类（不同组可使用不同阈值和告警渠道）
nodes:
  # 控制平面节点
  - hostname: k8s-master-01
    ip: 10.0.1.10
    port: 22
    username: root
    key_file: ~/.ssh/id_rsa
    groups: ["control-plane", "production"]
    # 可覆盖全局检查开关
    checks:
      disk: true
      memory: true
      conntrack: true
      kubelet: true
      kernel: true

  # 工作节点
  - hostname: k8s-worker-01
    ip: 10.0.1.21
    port: 22
    username: root
    key_file: ~/.ssh/id_rsa
    groups: ["worker", "production"]
    # k8s_node_name: cn-beijing.k8s-worker-01  # K8s Node 名称（与 hostname 不同时指定）

  # 使用跳板机的节点
  - hostname: k8s-worker-02
    ip: 10.0.2.21
    port: 22
    username: root
    key_file: ~/.ssh/id_rsa
    groups: ["worker", "production"]
    bastion:
      hostname: jump-server
      ip: 10.0.0.1
      port: 22
      username: ops
      key_file: ~/.ssh/id_rsa

# 全局并发数
concurrency: 5

# SSH 超时（秒）
ssh_timeout: 15
```

> **k8s_node_name 字段：** ACK/TKE 等托管 K8s 集群中，节点主机名（hostname）与 K8s Node 名称常不一致（如云厂商添加前缀），导致 kubelet 的 Node Ready 检查静默失败。设置 `k8s_node_name` 可覆盖 `kubectl get node` 查询时使用的名称。

### 告警阈值 (`config/thresholds.yaml`)

```yaml
# 每个检查项定义 WARNING 和 CRITICAL 两级阈值
# 超过 WARNING 发飞书普通消息，超过 CRITICAL 发飞书 + 钉钉 @all
disk:
  mount_points: ["/", "/var/lib/kubelet", "/var/lib/containerd"]
  space:
    warning_pct: 80
    critical_pct: 90
  inode:
    warning_pct: 80
    critical_pct: 90
  io_latency_ms:        # 可选，不存在 iostat 时自动跳过
    warning: 50
    critical: 100

memory:
  available:
    warning_pct: 20    # 可用内存低于 20% 告警
    critical_pct: 10
  swap:
    warning_pct: 10
    critical_pct: 30
  oom_window_minutes: 15  # 检索过去 N 分钟的 OOM 事件

conntrack:
  table_usage:
    warning_pct: 85
    critical_pct: 95
  time_wait_max: 10000

kubelet:
  pleg_latency_seconds:
    warning: 2.0
    critical: 5.0
  log_scan_window_minutes: 15
  log_error_patterns:
    - "error"
    - "timeout"
    - "deadline"
    - "backoff"
    - "eviction"

kernel:
  dmesg_critical_patterns:
    - "BUG:"
    - "Kernel panic"
    - "segfault"
    - "Hardware Error"
    - "WARNING:"
  hung_task_timeout: 120  # hung_task 超过此秒数告警
```

### 告警路由 (`config/alerting.yaml`)

```yaml
# 飞书 Webhook
feishu:
  enabled: true
  webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
  signing_key: ""         # 飞书机器人安全设置 → 签名校验 → 复制密钥（可选但推荐）
  level_routing:          # 按告警级别路由
    warning: true
    critical: true

# 钉钉 Webhook
dingtalk:
  enabled: true
  webhook_url: "https://oapi.dingtalk.com/robot/send?access_token=xxx"
  signing_key: ""         # 钉钉机器人安全设置 → 加签 → 复制密钥（可选但推荐）
  level_routing:
    warning: false        # WARNING 级别不发钉钉，避免打扰
    critical: true        # CRITICAL 级别双通道推送

# 分组路由（生产节点 CRITICAL 告警才飞书 + 钉钉，测试节点仅飞书 WARNING）
group_routing:
  production:
    feishu: ["warning", "critical"]
    dingtalk: ["critical"]
  staging:
    feishu: ["warning", "critical"]
    dingtalk: []          # staging 不发钉钉
```

> **分组路由规则：** 告警按节点所属分组（`groups` 字段）匹配路由规则。若节点属于多个分组，任一匹配即路由。若节点所属分组均未配置某渠道的路由规则，回退至渠道级 `level_routing` 默认配置。

> **获取 Webhook URL 和签名密钥：**
> - **飞书：** 群聊 → 设置 → 群机器人 → 添加自定义机器人 → 复制 Webhook URL；安全设置中选择"签名校验"获取 `signing_key`
> - **钉钉：** 群聊 → 设置 → 智能群助手 → 添加机器人 → 自定义 → 复制 access_token；安全设置中选择"加签"获取 `signing_key`

### 环境变量

| 变量 | 默认值 | 说明 |
|------|-------|------|
| `NHW_CONFIG_DIR` | `./config` | 配置文件目录 |

> **注意：** `--state-file`、`--log-level`、`--log-format` 为 CLI 参数，非环境变量。
>
> | CLI 参数 | 默认值 | 说明 |
> |---------|-------|------|
> | `--state-file` | `None`（仅内存） | 告警去重状态持久化 JSON 文件路径 |
> | `--log-level` | `INFO` | 日志级别（DEBUG/INFO/WARNING/ERROR） |
> | `--log-format` | `plain` | 日志格式（`plain` / `json`） |

---

## 告警消息格式

### 飞书消息卡片

巡检完成后，按节点汇总所有异常，单次巡检仅推送一条消息卡片，避免消息碎片化。

```
🏥 K8s 节点健康巡检 2026-05-24 10:15:32

🔴 CRITICAL (2)
├─ [node-1] conntrack: 表使用率 = 97% (阈值: 95%)
├─ [node-4] kernel: EXT4-fs error (device sdb1)

⚠️ WARNING (3)
├─ [node-1] disk: /var/lib/kubelet = 86% (阈值: 80%)
├─ [node-2] memory: 过去 15 分钟内检测到 3 次 OOM Kill
├─ [node-3] kubelet: PLEG 延迟 3.2s (阈值: 2s)

✅ 正常: 2 个节点
📊 巡检耗时: 4.2s
```

### 恢复通知

```
✅ 节点健康恢复通知

[node-1] conntrack 表使用率已恢复: 97% → 72%
[node-4] kernel EXT4-fs error 已恢复
```

---

## 开发

```bash
# 克隆并创建虚拟环境
git clone https://github.com/noneedtostudy/node-health-watcher.git
cd node-health-watcher
python -m venv .venv && source .venv/bin/activate

# 开发模式安装
pip install -e ".[dev]"

# 代码检查
ruff check node_health_watcher/ tests/

# 格式化
ruff format node_health_watcher/ tests/

# 运行测试
pytest tests/ -v --cov=node_health_watcher --cov-report=term-missing

# 干运行验证（不需要真实节点，用 mock）
python -m node_health_watcher --dry-run
```

### 编写检查插件

所有检查插件继承 `checks.base.BaseCheck`，实现三个方法即可接入调度器与告警链路：

```python
from node_health_watcher.checks.base import BaseCheck, CheckResult, CheckLevel
from node_health_watcher.config import register_check

@register_check("my_check")
class MyCheck(BaseCheck):
    name = "my_check"
    description = "自定义检查项"

    @classmethod
    def default_thresholds(cls) -> dict:
        """返回该检查项的默认阈值，用户可通过 thresholds.yaml 覆盖。"""
        return {
            "warning": 100,
            "critical": 200,
        }

    def probe_commands(self) -> dict[str, str]:
        """返回需要在目标节点上执行的命令字典。键为子项名，值为 shell 命令。"""
        return {
            "custom_metric": "cat /proc/sys/custom/metric",
        }

    def parse(self, hostname: str, outputs: dict[str, str]) -> list[CheckResult]:
        """解析命令输出，返回 CheckResult 列表。"""
        value = int(outputs["custom_metric"].strip())
        level = CheckLevel.WARNING if value > self.thresholds["warning"] else CheckLevel.OK
        return [
            CheckResult(
                hostname=hostname,
                category=self.name,
                sub_check="custom_metric",
                level=level,
                value=str(value),
                message=f"custom_metric = {value}",
            )
        ]
```

三个方法说明：

| 方法 | 用途 |
|------|------|
| `default_thresholds()` | 类方法，返回该检查项的默认阈值字典。用户通过 `thresholds.yaml` 的同名字段可覆盖其中任意值。 |
| `probe_commands()` | 返回 `{子项名: shell 命令}` 映射。命令在目标节点上以只读方式执行。 |
| `parse()` | 解析命令输出，返回 `CheckResult` 列表。`self.thresholds` 已合并默认值与用户配置。 |

`config.py` 中的检查注册表（`@register_check` 装饰器）管理插件的发现与加载——添加新检查项无需修改调度器代码。

---

## 贡献

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feat/my-feature`
3. 确保 ruff 和 pytest 在本地通过
4. 向 `main` 分支发起 Pull Request

所有 PR 将通过 GitHub Actions 自动执行代码静态检查与单元测试。

---

## 许可证

MIT © 2026 [Shaohan He](https://github.com/noneedtostudy)

---

## English

## Overview

**Node Health Watcher** is a centralized scheduled inspection and instant-messaging alerting tool for Kubernetes node fleets. It combines APScheduler-driven periodic checks, paramiko-based SSH remote inspection, multi-level threshold alerting, and stateful deduplication — so operators never need to SSH into individual nodes just to check if everything is healthy.

### Why Node Health Watcher?

[node-guardian](https://github.com/noneedtostudy/node-guardian) answers "how to troubleshoot" — when you SSH into a broken node, it provides a suite of diagnostic, hardening, and audit tools. But it doesn't answer "when to troubleshoot" — you can't manually SSH into 50 nodes at 3 AM every night.

**Node Health Watcher fills this gap:** zero manual login required. Scheduled inspection → SSH checks → threshold comparison → alert to Feishu/DingTalk if anomalous, stay silent if healthy. You only hear about problems that need your attention.

### Core Principles

| Principle | Implementation |
|-----------|---------------|
| **Centralized Scheduling** | Single deployment, APScheduler cron-driven tasks, no agent installation on target nodes |
| **Tiered Alerting** | WARNING / CRITICAL thresholds per check, routable to different IM channels by severity |
| **Deduplication & Suppression** | Stateful alert dedup: same node, same check only fires the first alert; recovery notification sent on resolution |
| **Extensible Checks** | Abstract-base-class check plugin system — add a new check without touching the scheduler or alert pipeline |
| **Defensive Execution** | SSH timeouts, auth failures, unreachable nodes all caught per-node; one node's failure never blocks the rest |

## Quick Start

```bash
# Clone the repository
git clone https://github.com/noneedtostudy/node-health-watcher.git
cd node-health-watcher

# Install dependencies
pip install -e .

# Initialize config files from templates
cp config/nodes.example.yaml config/nodes.yaml
cp config/thresholds.example.yaml config/thresholds.yaml
cp config/alerting.example.yaml config/alerting.yaml

# Edit to match your environment
vim config/nodes.yaml
vim config/alerting.yaml

# Dry run — execute a full inspection without sending alerts
python -m node_health_watcher --dry-run

# Start the scheduler (default: every 5 minutes)
python -m node_health_watcher --interval 5m

# Single inspection with alerts
python -m node_health_watcher --once

# Custom cron expression
python -m node_health_watcher --cron "*/10 * * * *"
```

### Prerequisites

- **Python 3.10+**
- **Control node** (the machine running this tool) must have SSH key-based passwordless access to all target K8s nodes
- **Target nodes** must run Linux (kernel 4.x+); no additional software required on targets
- **Optional:** `cryptography` (Ed25519 key support), `rich` (colored terminal output)
- **journald persistence:** Chinese cloud server default images often disable persistent journald. Enable `Storage=persistent` in `/etc/systemd/journald.conf` on target nodes if possible. Kubelet log scanning (PLEG latency, error logs) automatically falls back to `/var/log/kubelet.log` when journald is unavailable.

## Health Checks

Every check category includes multiple sub-checks. Each sub-check is independently evaluated and alerted. All checks are executed read-only via SSH on target nodes — no system state is ever modified.

### disk — Disk Health

```
Scope: disk space, inode usage, critical mount points, I/O latency
```

**Sub-checks:**
1. **Space usage** — traverses configured mount points (default: `/`, `/var/lib/kubelet`, `/var/lib/containerd`), alerts on threshold breach
2. **Inode usage** — inode exhaustion is harder to diagnose than space exhaustion; independently tracked
3. **Read-only filesystem** — detects if any critical mount point has been unexpectedly remounted read-only
4. **Disk I/O latency** (optional) — average disk wait time via `iostat`, gracefully skipped if unavailable

**Example output:**
```
[2026-05-24 10:15:32] [OK] [node-1] disk: / = 62% (thresholds: 80%/90%)
[2026-05-24 10:15:32] [WARN] [node-1] disk: /var/lib/kubelet = 86% (thresholds: 80%/90%)
[2026-05-24 10:15:32] [OK] [node-1] disk: inode / = 34% (thresholds: 80%/90%)
[2026-05-24 10:15:32] [OK] [node-1] disk: /var/lib/kubelet read-write OK
```

### memory — Memory Health

```
Scope: available memory, swap, OOM events
```

**Sub-checks:**
1. **Available memory** — `MemAvailable` below threshold triggers alert (more accurate than `MemFree` for gauging usable memory)
2. **Swap usage** — K8s nodes should have swap disabled or near-zero; swap activity signals memory pressure
3. **Recent OOM events** — scans `journalctl` / `dmesg` for OOM Kill records within the configured time window
4. **Top-N memory consumers** (optional) — lists highest-memory PIDs and their corresponding Pods

### conntrack — Connection Tracking

```
Scope: conntrack table utilization, connection statistics
```

**Sub-checks:**
1. **Table usage (two-tier alert)** — ≥85% WARNING / ≥95% CRITICAL, calculated against `conntrack_max`
2. **Table overflow drops** — checks `nf_conntrack_count` / `nf_conntrack_max` ratio and `nf_conntrack_drop` counter
3. **TIME_WAIT pile-up** — in high-throughput short-lived-connection workloads, TIME_WAIT buildup precedes conntrack exhaustion

### kubelet — Kubelet Health

```
Scope: service status, node readiness, critical logs
```

**Sub-checks:**
1. **Service status** — `systemctl is-active kubelet`; non-active triggers immediate alert
2. **Node readiness** — checks Node Ready condition via `kubectl` (uses `k8s_node_name` if set, falls back to `hostname`)
3. **PLEG latency** — scans kubelet logs for PLEG (Pod Lifecycle Event Generator) latency warnings (tries journalctl first, falls back to /var/log/kubelet.log)
4. **Recent critical errors** — filters `error|timeout|deadline|backoff|eviction` patterns within the configured time window (same journald → file fallback)

### kernel — Kernel Anomalies

```
Scope: kernel log exceptions, hung tasks, filesystem errors
```

**Sub-checks:**
1. **dmesg critical events** — scans for `BUG|panic|segfault|WARNING|Hardware Error` patterns
2. **Hung task detection** — kernel hung_task timeout events, typically indicating I/O blockage or deadlocks
3. **EXT4/XFS errors** — filesystem-level I/O errors and metadata corruption warnings
4. **Kernel oops counter** — tracks changes in the kernel oops count since boot

## Architecture

```
.
├── node_health_watcher/        # Application package
│   ├── __init__.py
│   ├── __main__.py             # CLI entry point (argparse)
│   ├── scheduler.py            # APScheduler orchestration engine
│   ├── config.py               # YAML config loading & validation
│   ├── checks/                 # Check plugins
│   │   ├── __init__.py
│   │   ├── base.py             # Abstract check base class (interface + result model)
│   │   ├── disk.py
│   │   ├── memory.py
│   │   ├── conntrack.py
│   │   ├── kubelet.py
│   │   └── kernel.py
│   ├── transport/              # Remote execution layer
│   │   ├── __init__.py
│   │   ├── ssh.py              # paramiko SSH client wrapper
│   │   └── executor.py         # ThreadPoolExecutor concurrency driver
│   └── alert/                  # Alerting output
│       ├── __init__.py
│       ├── common.py           # Shared Feishu/DingTalk format helpers
│       ├── feishu.py           # Feishu webhook push
│       ├── dingtalk.py         # DingTalk webhook push
│       └── dedup.py            # Stateful alert deduplication & recovery detection
├── config/                     # Configuration files
│   ├── nodes.example.yaml
│   ├── thresholds.example.yaml
│   └── alerting.example.yaml
├── tests/                      # pytest unit tests
│   ├── conftest.py             # Shared fixtures (mock SSH, fake nodes)
│   ├── test_disk.py
│   ├── test_memory.py
│   ├── test_conntrack.py
│   ├── test_kubelet.py
│   ├── test_kernel.py
│   └── test_dedup.py
├── .github/workflows/
│   └── ci.yml                  # CI: ruff lint + pytest + coverage
├── pyproject.toml
└── README.md
```

### Design Decisions

**Why Python instead of continuing with Bash?**

node-guardian chose Bash because it runs on the target node with zero runtime dependencies. Node Health Watcher runs on a control node and requires: structured config parsing (YAML) → concurrent SSH to multiple nodes → structured output parsing → JSON payload construction for IM webhooks. Every step in this pipeline works with structured data — Python is the natural fit. Additionally, APScheduler cron scheduling, thread-pool concurrency, and IM webhook signing calculations would quickly become unwieldy in Bash.

**Why paramiko + ThreadPoolExecutor instead of asyncssh?**

For fleets up to ~100 nodes, 5-10 threads in a ThreadPoolExecutor are sufficient — the bottleneck is command execution time on the target, not SSH handshake overhead. paramiko is the most mature option in the pure-Python ecosystem, with comprehensive documentation and well-tested support for enterprise environments (jump hosts, proxies, PKey). If scaling to 500+ nodes becomes necessary, migration to asyncssh is a manageable effort.

**Why APScheduler instead of Linux cron?**

- **In-process scheduling**: no dependency on system crond, single-process deployment, container-friendly
- **Misfire handling**: three strategies for backlogged jobs — drop, coalesce, or fire immediately
- **Timezone-aware**: cron expressions natively timezone-aware, no UTC vs. local-time confusion
- **Dynamic tasks**: check jobs can be added or removed at runtime without restarting the process

**Alert deduplication**

First detection of an anomaly → fire alert and record state (node + check + sub-check + level). Subsequent inspections where the same anomaly persists → suppress (do not re-send), log only. Anomaly clears → fire a recovery notification and remove the state record.

State is stored in-memory by default (lightweight, zero-dependency). Use `--state-file` to specify a JSON file path for state persistence across process restarts.

**Why multi-sub-check per category?**

A single metric can tell you "disk is full" but not "why it's full", "whether it just filled up", or "whether inodes are exhausted instead of space." Multi-dimensional sub-checks produce actionable alerts. Example: 90% disk + 35% inode → a single large file write. 62% disk + 92% inode → massive small-file creation. The troubleshooting path is completely different.

**Dry-run mode**

`--dry-run` executes the full inspection pipeline (SSH connection → command execution → result parsing) but skips alert delivery. Use it to: validate configuration before going live, fine-tune thresholds, or verify SSH connectivity. Dry-run output is identical to normal mode, with `[DRY-RUN]` annotation in the log.

## Configuration

### Node Inventory (`config/nodes.yaml`)

```yaml
nodes:
  - hostname: k8s-master-01
    ip: 10.0.1.10
    port: 22
    username: root
    key_file: ~/.ssh/id_rsa
    groups: ["control-plane", "production"]
    checks:
      disk: true
      memory: true
      conntrack: true
      kubelet: true
      kernel: true

  - hostname: k8s-worker-01
    ip: 10.0.1.21
    port: 22
    username: root
    key_file: ~/.ssh/id_rsa
    groups: ["worker", "production"]
    # k8s_node_name: cn-beijing.k8s-worker-01  # K8s Node name (when different from hostname)

  # Behind a jump host
  - hostname: k8s-worker-02
    ip: 10.0.2.21
    port: 22
    username: root
    key_file: ~/.ssh/id_rsa
    groups: ["worker", "production"]
    bastion:
      hostname: jump-server
      ip: 10.0.0.1
      port: 22
      username: ops
      key_file: ~/.ssh/id_rsa

concurrency: 5
ssh_timeout: 15
```

### Alert Thresholds (`config/thresholds.yaml`)

```yaml
disk:
  mount_points: ["/", "/var/lib/kubelet", "/var/lib/containerd"]
  space:
    warning_pct: 80
    critical_pct: 90
  inode:
    warning_pct: 80
    critical_pct: 90
  io_latency_ms:
    warning: 50
    critical: 100

memory:
  available:
    warning_pct: 20
    critical_pct: 10
  swap:
    warning_pct: 10
    critical_pct: 30
  oom_window_minutes: 15

conntrack:
  table_usage:
    warning_pct: 85
    critical_pct: 95
  time_wait_max: 10000

kubelet:
  pleg_latency_seconds:
    warning: 2.0
    critical: 5.0
  log_scan_window_minutes: 15
  log_error_patterns:
    - "error"
    - "timeout"
    - "deadline"
    - "backoff"
    - "eviction"

kernel:
  dmesg_critical_patterns:
    - "BUG:"
    - "Kernel panic"
    - "segfault"
    - "Hardware Error"
    - "WARNING:"
  hung_task_timeout: 120
```

### Alert Routing (`config/alerting.yaml`)

```yaml
feishu:
  enabled: true
  webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
  signing_key: ""
  level_routing:
    warning: true
    critical: true

dingtalk:
  enabled: true
  webhook_url: "https://oapi.dingtalk.com/robot/send?access_token=xxx"
  signing_key: ""
  level_routing:
    warning: false
    critical: true

group_routing:
  production:
    feishu: ["warning", "critical"]
    dingtalk: ["critical"]
  staging:
    feishu: ["warning", "critical"]
    dingtalk: []
```

> **How to get the webhook URL and signing key:**
> - **Feishu:** Group chat → Settings → Bots → Add Custom Bot → Copy Webhook URL; under Security Settings select "Signature verification" to get the `signing_key`
> - **DingTalk:** Group chat → Settings → Smart Assistant → Add Bot → Custom → Copy access_token; under Security Settings select "Signing" to get the `signing_key`

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NHW_CONFIG_DIR` | `./config` | Configuration directory |

> **Note:** `--state-file`, `--log-level`, and `--log-format` are CLI flags, not environment variables.
>
> | CLI Flag | Default | Description |
> |---------|---------|-------------|
> | `--state-file` | `None` (in-memory only) | Alert dedup state persistence JSON file path |
> | `--log-level` | `INFO` | Log level (DEBUG/INFO/WARNING/ERROR) |
> | `--log-format` | `plain` | Log format (`plain` / `json`) |

## Alert Message Format

### Feishu Card Message

After each inspection, all anomalies are aggregated per node and pushed as a single card message — no message fragmentation.

```
🏥 K8s Node Health Inspection 2026-05-24 10:15:32

🔴 CRITICAL (2)
├─ [node-1] conntrack: table usage = 97% (threshold: 95%)
├─ [node-4] kernel: EXT4-fs error (device sdb1)

⚠️ WARNING (3)
├─ [node-1] disk: /var/lib/kubelet = 86% (threshold: 80%)
├─ [node-2] memory: 3 OOM Kills in last 15min
├─ [node-3] kubelet: PLEG latency 3.2s (threshold: 2s)

✅ Healthy: 2 nodes
📊 Inspection duration: 4.2s
```

### Recovery Notification

```
✅ Node Health Recovery

[node-1] conntrack table usage recovered: 97% → 72%
[node-4] kernel EXT4-fs error recovered
```

## Development

```bash
git clone https://github.com/noneedtostudy/node-health-watcher.git
cd node-health-watcher
python -m venv .venv && source .venv/bin/activate

# Editable install with dev dependencies
pip install -e ".[dev]"

# Lint
ruff check node_health_watcher/ tests/

# Format
ruff format node_health_watcher/ tests/

# Test with coverage
pytest tests/ -v --cov=node_health_watcher --cov-report=term-missing

# Dry-run validation (no real nodes needed, uses mock)
python -m node_health_watcher --dry-run
```

### Writing a Check Plugin

All check plugins inherit from `checks.base.BaseCheck`. Implement three methods to integrate into the scheduler and alerting pipeline:

```python
from node_health_watcher.checks.base import BaseCheck, CheckResult, CheckLevel
from node_health_watcher.config import register_check

@register_check("my_check")
class MyCheck(BaseCheck):
    name = "my_check"
    description = "Custom health check"

    @classmethod
    def default_thresholds(cls) -> dict:
        """Return default thresholds for this check; overridable via thresholds.yaml."""
        return {
            "warning": 100,
            "critical": 200,
        }

    def probe_commands(self) -> dict[str, str]:
        """Return a dict of sub-check name → shell command."""
        return {
            "custom_metric": "cat /proc/sys/custom/metric",
        }

    def parse(self, hostname: str, outputs: dict[str, str]) -> list[CheckResult]:
        """Parse command outputs, return CheckResult list."""
        value = int(outputs["custom_metric"].strip())
        level = CheckLevel.WARNING if value > self.thresholds["warning"] else CheckLevel.OK
        return [
            CheckResult(
                hostname=hostname,
                category=self.name,
                sub_check="custom_metric",
                level=level,
                value=str(value),
                message=f"custom_metric = {value}",
            )
        ]
```

The three methods explained:

| Method | Purpose |
|--------|---------|
| `default_thresholds()` | Classmethod returning the check's default threshold dict. Users can override any value via `thresholds.yaml` under the same key. |
| `probe_commands()` | Returns `{sub_check_name: shell_command}` mapping. Commands are executed read-only on target nodes. |
| `parse()` | Parses command outputs into a `CheckResult` list. `self.thresholds` already contains merged defaults and user config. |

The check registry in `config.py` (`@register_check` decorator) handles plugin discovery and loading — add a new check without touching the scheduler.

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Ensure ruff and pytest pass locally
4. Open a pull request against `main`

All PRs are automatically linted and tested via GitHub Actions.

## License

MIT © 2026 [Shaohan He](https://github.com/noneedtostudy)
