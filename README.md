<p align="center">
  <h1 align="center">🏥 Node Health Watcher</h1>
  <p align="center"><strong>Kubernetes 节点定时巡检与 IM 告警中心 / Scheduled Node Health Inspection & Alerting</strong></p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License">
  <img src="https://img.shields.io/badge/platform-linux%2Famd64-lightgrey" alt="Linux/amd64">
  <img src="https://github.com/290298661-pixel/node-health-watcher/actions/workflows/ci.yml/badge.svg" alt="CI">
</p>

## 目录 / Table of Contents

- [概述](#概述)
- [快速开始](#快速开始)
- [架构](#架构)
- [配置说明](#配置说明)
- [告警消息格式](#告警消息格式)
- [开发](#开发)
- [贡献](#贡献)
- [许可证](#许可证)
- [English](#english)

---

## 概述

**Node Health Watcher** 定时 SSH 进你的 K8s 节点，跑磁盘、内存、conntrack、kubelet、内核五项巡检，发现有异常就推飞书/钉钉——有状态的去重机制保证同一个问题只告警一次，恢复了还通知你。你不需要半夜起来手动 SSH 进 50 台节点。

### 它在工具链中的位置

Node Health Watcher 是 [三部曲](https://github.com/290298661-pixel) 的**第二环**——"检测层"：

| 项目 | 语言 | 回答的问题 |
|------|------|-----------|
| [Node Guardian](https://github.com/290298661-pixel/node-guardian) | Bash | 出了故障怎么排查和修复？ |
| **Node Health Watcher** ← 你在这里 | Python | 什么时候该去排查？ |
| [Game Fleet Director](https://github.com/290298661-pixel/game-server-orchestrator) | Go | 谁来操作游戏服本身？ |

**选 Python 是因为** 这一层跑在控制节点上，需要 YAML 配置解析、并发 SSH 多节点、结构化解析输出、构造 JSON 推 IM webhook——每一步都在处理结构化数据，Python 天然适合。paramiko + APScheduler 的组合在百台节点规模内足够用，且文档丰富、企业环境对接方案完备。

### 核心原则

| 原则 | 实现 |
|------|------|
| **无 agent** | 中心化调度，目标节点不需要装任何东西 |
| **分级告警** | WARNING / CRITICAL 两级阈值，可按级别路由到不同 IM 渠道 |
| **去重抑制** | 同一异常只发首条，恢复后发恢复通知，不刷屏 |
| **插件化** | 基于 ABC 的检查插件体系，加新巡检项不用碰调度器和告警代码 |
| **防御性执行** | SSH 超时、认证失败、节点不可达全链路捕获，单节点失败不阻塞其余 |

---

## 快速开始

```bash
git clone https://github.com/290298661-pixel/node-health-watcher.git && cd node-health-watcher
pip install -e .

# 初始化配置（按实际环境编辑）
cp config/nodes.example.yaml config/nodes.yaml
cp config/thresholds.example.yaml config/thresholds.yaml
cp config/alerting.example.yaml config/alerting.yaml
vim config/nodes.yaml config/alerting.yaml

# 干运行 — 巡检一轮，只看不告
python -m node_health_watcher --dry-run

# 单次巡检 + 发送告警
python -m node_health_watcher --once

# 启动定时调度（每 5 分钟）
python -m node_health_watcher --interval 5m
```

**环境：** Python 3.10+ · 控制节点需 SSH 免密到目标节点 · 目标节点无需安装额外组件

---

## 架构

```
.
├── node_health_watcher/        # 应用主包
│   ├── __main__.py             # CLI 入口
│   ├── scheduler.py            # APScheduler 编排引擎
│   ├── config.py               # YAML 配置加载与校验
│   ├── checks/                 # 检查插件（5 项）
│   │   ├── base.py             # ABC 基类 + CheckResult 模型
│   │   ├── disk.py             # 空间 / inode / 只读 / I/O
│   │   ├── memory.py           # MemAvailable / swap / OOM
│   │   ├── conntrack.py        # 表使用率 / drop / TIME_WAIT
│   │   ├── kubelet.py          # 服务状态 / Ready / PLEG / 错误日志
│   │   └── kernel.py           # dmesg / hung_task / FS 错误
│   ├── transport/
│   │   ├── ssh.py              # paramiko SSH + 跳板机支持
│   │   └── executor.py         # ThreadPoolExecutor 并发调度
│   └── alert/
│       ├── feishu.py           # 飞书交互式卡片
│       ├── dingtalk.py         # 钉钉 Markdown
│       └── dedup.py            # 有状态去重 + 恢复检测
├── config/                     # 配置模板（nodes / thresholds / alerting）
├── tests/                      # pytest（7 个文件，覆盖率 >85%）
└── .github/workflows/ci.yml    # CI：ruff + pytest 矩阵（3.10-3.12）
```

### 设计决策

**为什么选 paramiko + ThreadPoolExecutor？** 百台节点内 5-10 线程足够——瓶颈在节点命令执行时间而非 SSH 握手。paramiko 是纯 Python 生态中最成熟的 SSH 库，对接跳板机、代理、PKey 方案完备。扩展到 500+ 节点时迁移 asyncssh 成本可控。

**为什么选 APScheduler 而不是 Linux cron？** 需要在代码内管理 cron 表达式和告警状态（去重字典）。cron 是进程级别的调度无状态，APScheduler 可以在同进程内直接读写 `DedupStore`，无需借助外部数据库或文件锁。

**国内云环境适配** —— kubelet 日志扫描优先 journalctl，自动回退 `/var/log/kubelet.log`（国内云镜像默认关闭 journald 持久化）。SSH 默认 `WarningPolicy` 而非 `AutoAddPolicy`，需显式设置 `NHW_INSECURE_AUTOADD_HOST_KEY` 才放开。

### 告警去重

首次异常 → 告警 + 记录状态。同一异常持续存在 → 抑制不重复发。异常恢复 → 发恢复通知 + 清除记录。状态默认内存 dict，`--state-file` 可指定 JSON 文件实现重启后保留。

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
git clone https://github.com/290298661-pixel/node-health-watcher.git
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

MIT © 2026 [Shaohan He](https://github.com/290298661-pixel)

---

## English

## Overview

**Node Health Watcher** is a centralized scheduled inspection and instant-messaging alerting tool for Kubernetes node fleets. It combines APScheduler-driven periodic checks, paramiko-based SSH remote inspection, multi-level threshold alerting, and stateful deduplication — so operators never need to SSH into individual nodes just to check if everything is healthy.

### Why Node Health Watcher?

[node-guardian](https://github.com/290298661-pixel/node-guardian) answers "how to troubleshoot" — when you SSH into a broken node, it provides a suite of diagnostic, hardening, and audit tools. But it doesn't answer "when to troubleshoot" — you can't manually SSH into 50 nodes at 3 AM every night.

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
git clone https://github.com/290298661-pixel/node-health-watcher.git
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
git clone https://github.com/290298661-pixel/node-health-watcher.git
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

MIT © 2026 [Shaohan He](https://github.com/290298661-pixel)
