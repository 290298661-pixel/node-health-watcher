# Node Health Watcher

> Scheduled Kubernetes node inspection and alerting service based on Python, SSH, and Feishu/DingTalk webhooks.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%2Famd64-lightgrey)](https://www.kernel.org/)

## 概述

Node Health Watcher 是一个集中式节点巡检工具。它在控制节点上运行，通过 SSH 连接 Kubernetes 节点，定期检查磁盘、内存、conntrack、kubelet 和内核日志，并按阈值发送飞书或钉钉告警。

当前重点：

- 通过配置文件维护节点清单、巡检阈值和告警路由。
- 使用 SSH 远程执行只读检查命令，目标节点不需要安装 agent。
- 对同一异常做状态去重，避免重复告警。
- 支持单次巡检、定时调度、dry-run 和 cron 表达式。
- 通过 pytest 和 ruff 保持基础质量。

## 架构

```text
Control node
    |
    v
Node Health Watcher
    |
    +-- scheduler
    +-- config loader
    +-- SSH executor
    +-- check plugins
    +-- alert dedup store
    |
    v
Kubernetes nodes
    |
    +-- disk
    +-- memory
    +-- conntrack
    +-- kubelet
    +-- kernel logs
    |
    v
Feishu / DingTalk
```

## 快速开始

### 前提条件

- Python 3.10+
- 控制节点可以通过 SSH 免密访问目标节点
- 目标节点运行 Linux
- 可选：`kubectl`、`journalctl`、`conntrack`、`iostat`

### 安装

```bash
git clone https://github.com/Shaohan-He/node-health-watcher.git
cd node-health-watcher
pip install -e .
```

### 初始化配置

```bash
cp config/nodes.example.yaml config/nodes.yaml
cp config/thresholds.example.yaml config/thresholds.yaml
cp config/alerting.example.yaml config/alerting.yaml
```

按实际环境编辑：

- `config/nodes.yaml`：节点地址、SSH 用户、密钥、分组和检查开关。
- `config/thresholds.yaml`：各巡检项的 WARNING / CRITICAL 阈值。
- `config/alerting.yaml`：飞书、钉钉 Webhook 和分组路由。

### 运行

```bash
# 只执行巡检，不发送告警
python -m node_health_watcher --dry-run

# 单次巡检并发送告警
python -m node_health_watcher --once

# 每 5 分钟巡检一次
python -m node_health_watcher --interval 5m

# 使用 cron 表达式
python -m node_health_watcher --cron "*/10 * * * *"
```

## 目录结构

```text
node-health-watcher/
├── node_health_watcher/
│   ├── __main__.py                    # CLI 入口
│   ├── scheduler.py                   # APScheduler 调度
│   ├── config.py                      # YAML 配置加载与校验
│   ├── checks/
│   │   ├── base.py                    # 检查插件基类和结果模型
│   │   ├── disk.py                    # 磁盘空间、inode、只读挂载、I/O
│   │   ├── memory.py                  # 可用内存、swap、OOM
│   │   ├── conntrack.py               # conntrack 表使用率和连接状态
│   │   ├── kubelet.py                 # kubelet 服务、Node Ready、日志
│   │   └── kernel.py                  # dmesg、hung task、文件系统错误
│   ├── transport/
│   │   ├── ssh.py                     # paramiko SSH 客户端
│   │   └── executor.py                # 并发执行器
│   └── alert/
│       ├── common.py                  # 告警格式公共逻辑
│       ├── feishu.py                  # 飞书 Webhook
│       ├── dingtalk.py                # 钉钉 Webhook
│       └── dedup.py                   # 告警去重与恢复检测
├── config/
│   ├── nodes.example.yaml
│   ├── thresholds.example.yaml
│   └── alerting.example.yaml
├── deploy/
│   └── deployment.yaml
├── tests/
├── pyproject.toml
└── .github/workflows/
    └── ci.yml
```

## 配置

### 节点清单

`config/nodes.yaml` 定义巡检目标和 SSH 连接信息：

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

concurrency: 5
ssh_timeout: 15
```

如果节点主机名和 Kubernetes Node 名称不一致，可以设置 `k8s_node_name`。

### 告警阈值

`config/thresholds.yaml` 定义各项检查的阈值：

```yaml
disk:
  mount_points: ["/", "/var/lib/kubelet", "/var/lib/containerd"]
  space:
    warning_pct: 80
    critical_pct: 90

memory:
  available:
    warning_pct: 20
    critical_pct: 10

conntrack:
  table_usage:
    warning_pct: 85
    critical_pct: 95
```

### 告警路由

`config/alerting.yaml` 定义通知渠道：

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
```

### 常用参数

| 参数 | 说明 |
| --- | --- |
| `--dry-run` | 执行巡检但不发送告警 |
| `--once` | 执行一次巡检后退出 |
| `--interval 5m` | 按固定间隔执行 |
| `--cron "*/10 * * * *"` | 按 cron 表达式执行 |
| `--state-file <path>` | 将告警去重状态持久化到 JSON 文件 |
| `--log-level DEBUG` | 设置日志级别 |
| `--log-format json` | 使用 JSON 日志 |

环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `NHW_CONFIG_DIR` | `./config` | 配置文件目录 |
| `NHW_INSECURE_AUTOADD_HOST_KEY` | 未启用 | 允许自动信任未知 SSH host key，生产环境谨慎使用 |

## 巡检项

| 巡检项 | 内容 |
| --- | --- |
| `disk` | 磁盘空间、inode、只读挂载、I/O 延迟 |
| `memory` | 可用内存、swap 使用、近期 OOM |
| `conntrack` | conntrack 表使用率、连接状态、TIME_WAIT |
| `kubelet` | kubelet 服务状态、Node Ready、PLEG 或错误日志 |
| `kernel` | dmesg 关键错误、hung task、文件系统错误 |

所有检查通过 SSH 只读命令执行，不主动修改目标节点状态。

## 告警去重

同一节点、同一检查项、同一异常只发送首条告警。异常持续存在时仅记录日志，不重复推送。异常恢复后发送恢复通知，并清除对应状态。

默认状态保存在内存中；需要跨进程重启保留时，使用：

```bash
python -m node_health_watcher --interval 5m --state-file ./data/dedup-state.json
```

## 设计取舍

| 主题 | 当前选择 | 说明 |
| --- | --- | --- |
| 远程执行 | paramiko SSH | 目标节点无需安装 agent，支持跳板机和密钥认证 |
| 并发模型 | ThreadPoolExecutor | 对几十到上百台节点足够，复杂度低 |
| 调度 | APScheduler | 支持 interval 和 cron，便于与去重状态放在同一进程 |
| 配置 | YAML | 节点、阈值和路由可读性较好 |
| 告警 | 飞书 / 钉钉 Webhook | 适合轻量通知场景 |

## 开发

```bash
git clone https://github.com/Shaohan-He/node-health-watcher.git
cd node-health-watcher
python -m venv .venv
. .venv/bin/activate

pip install -e ".[dev]"
ruff check node_health_watcher/ tests/
ruff format node_health_watcher/ tests/
pytest tests/ -v --cov=node_health_watcher --cov-report=term-missing
```

## 扩展检查项

新增检查项时，继承 `checks.base.BaseCheck`，实现：

| 方法 | 用途 |
| --- | --- |
| `default_thresholds()` | 返回默认阈值 |
| `probe_commands()` | 返回需要在目标节点执行的只读命令 |
| `parse()` | 将命令输出转换为 `CheckResult` |

使用 `@register_check("name")` 注册后，配置文件即可启用该检查项。

## 相关项目

| 仓库 | 关系 |
| --- | --- |
| [node-guardian](https://github.com/Shaohan-He/node-guardian) | 节点异常后的人工诊断与维护工具 |
| [fleet-observability](https://github.com/Shaohan-He/fleet-observability) | 可接入巡检指标、日志和告警 |
| [fleet-gitops](https://github.com/Shaohan-He/fleet-gitops) | 可管理本服务的部署配置 |
| [k8s-healing-agent](https://github.com/Shaohan-He/k8s-healing-agent) | 可消费节点告警作为修复输入 |

## License

MIT © 2026 [Shaohan He](https://github.com/Shaohan-He)
