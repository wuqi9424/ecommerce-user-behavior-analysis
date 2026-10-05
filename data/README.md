# 数据来源与本地准备

## 1. 数据来源

本项目使用 [Alibaba Tianchi / Taobao UserBehavior 数据集](https://tianchi.aliyun.com/dataset/649)，原始文件名为 **UserBehavior.csv**。请自行通过来源页面下载、解压，并遵循其访问要求和使用条款；本仓库不分发数据。

## 2. 原始字段与行为类型

CSV 无表头，字段顺序如下：

| 字段 | 含义 | 本项目读取类型 |
|---|---|---|
| `user_id` | 用户标识 | BIGINT |
| `item_id` | 商品标识 | BIGINT |
| `category_id` | 品类标识 | BIGINT |
| `behavior_type` | 行为类型 | VARCHAR |
| `timestamp` | Unix 秒级时间戳 | BIGINT |

| behavior_type | 含义 |
|---|---|
| `pv` | 浏览 |
| `fav` | 收藏 |
| `cart` | 加购 |
| `buy` | 购买行为事件，不是订单 |

## 3. 本项目分析范围

- 时间：**2017-11-25 至 2017-12-03**，共九个自然日。
- 时区：**Asia/Shanghai**。
- 清洗表：`user_behavior_clean`，其中 `event_time` 为转换后的上海本地时间。
- 当前已验证数据版本：**100,095,182 条清洗后事件、987,991 位用户**。原始记录数与清洗后记录数不是同一口径。

## 4. 本地目录

以下路径均相对于项目根目录：

```text
data/
├── README.md
├── raw/
│   └── UserBehavior.csv
└── processed/
    └── ecommerce.duckdb
```

原始 CSV 约 **3.4 GB**，当前 DuckDB 数据库约 **1.3 GB**，两者均不提交到 GitHub，已由 [.gitignore](../.gitignore) 的 `data/raw/*`、`data/processed/*` 等规则忽略。此数据说明文档不受这些规则影响，可以提交。

请将自行下载并解压的文件放入 `data/raw/`，不要将压缩包或其他文件名直接传给现有构建 SQL。初次构建还需额外内存与临时磁盘空间。

## 5. 数据构建与分析顺序

从项目根目录执行，以便 SQL 中的相对数据路径正确解析。Python 环境、依赖安装与可执行命令见 [项目 README 的运行说明](../README.md#12-如何运行)。

1. 下载并解压 `UserBehavior.csv`，放入 `data/raw/`；准备 `data/processed/` 目录。
2. 在 `data/processed/ecommerce.duckdb` 上运行 [01_create_tables.sql](../sql/01_create_tables.sql)。它创建原始 CSV 视图，转换时间、过滤分析范围与行为类型，并对完全相同日志去重。**该步骤会重建清洗表。**
3. 运行 [02_data_quality_checks.sql](../sql/02_data_quality_checks.sql)，检查规模、时间范围、缺失、行为类型与重复记录；其中原始数据检查仍依赖 CSV。
4. 运行 SQL [03 平台分析](../sql/03_platform_overview.sql)、[04 转化漏斗](../sql/04_conversion_funnel.sql)、[05 留存与重复购买](../sql/05_retention_repeat_behavior.sql)、[06 用户分层](../sql/06_user_segmentation.sql)。这些分析模块不修改源表。
5. 执行 [用户分层可视化 Notebook](../notebooks/05_user_segmentation_visualization.ipynb)。它以只读连接复现 SQL 06 并校验人数；若数据版本不同导致校验失败，应先定位差异，不直接移除断言。

完全重复日志的去重属于分析假设，不意味着同一用户的所有重复行为都应删除。

## 6. 数据限制

- 无 `price` 或交易金额，不能计算 GMV、Monetary，也不能据此进行标准 RFM 或 CLV 分析。
- 无 `order_id`，不能将 buy event 当作真实订单，也不能推断真实订单数或订单复购率。
- 无 `session_id`，不支持直接还原完整会话；同用户同商品的时间顺序路径仅是窗口内观察到的行为序列，不代表完整订单链路或因果关系。
- 仅九天，窗前历史与窗后行为不可见。首次观测日期不是注册日期；留存和跨日重复购买受左右截尾影响，不能解释为真实长期留存、长期复购或长期流失率。
- 数据是公开样本，不能直接推广为平台全量情况，也不能证明运营策略有效。
