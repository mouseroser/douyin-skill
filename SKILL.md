---
name: douyin
description: 抖音创作者中心 CDP 自动化发布 skill。用于自动化发布短视频到抖音 creator 平台，处理视频上传、双封面、背景音乐、话题标签和审核等待。优先用于 wemedia 流水线 Step 7.5 中由 main 调用的抖音发布执行；也可在晨星明确要求单独发布抖音内容时使用。
---

# 抖音 CDP 自动化发布

> 定位更新：**这是 wemedia 流水线的发布执行节点，不是独立绕过编排的创作入口。**
>
> 标准用法：wemedia 在 Step 6 完成抖音适配产物，晨星在 Step 7 确认后，**由 main 在 Step 7.5 调用本 skill** 执行发布与审核等待。

## 与 wemedia 的联动关系

```text
Step 3-6（wemedia）
  → 产出 Douyin Publish Pack
  → 包含：title / description / video_path / vertical_cover_path / horizontal_cover_path / music / visibility
  ↓
Step 7（晨星确认）
  ↓
Step 7.5（main）
  → 校验发布包
  → 调用 douyin/scripts/publish_douyin.py
  → 完成上传 / 填表 / 双封面 / 音乐 / 提交 / 审核等待
  ↓
main 回报审核结果
```

**统一 schema**：`~/.openclaw/workspace/shared-context/DOUYIN-PUBLISH-PACK-SCHEMA.md`

**不要**把本 skill 当作内容策划或文案生成工具。

## 快速开始

```bash
python3 ~/.openclaw/skills/douyin-skill/scripts/publish_douyin.py \
  --pack /path/to/douyin-pack.md \
  --step validate_pack

python3 ~/.openclaw/skills/douyin-skill/scripts/publish_douyin.py \
  --pack /path/to/douyin-pack.md \
  --step full
```

或直接传参数：

```bash
python3 ~/.openclaw/skills/douyin-skill/scripts/publish_douyin.py \
  --video /path/to/video.mp4 \
  --title "视频标题" \
  --description "视频描述，带#话题标签" \
  --vertical-cover /path/to/cover_9x16.png \
  --horizontal-cover /path/to/cover_4x3.png \
  --music "热门" \
  --visibility private \
  --step full
```

## 核心参数

| 参数 | 必须 | 对应发布包字段 | 说明 |
|------|------|------|------|
| `--video` | ✅ | `video_path` | 视频文件路径（mp4，建议成片已验收） |
| `--title` | ✅ | `title` | 视频标题（≤30字） |
| `--description` | ✅ | `description` | 视频描述，正文末尾带 `#标签名` |
| `--vertical-cover` | ✅ | `vertical_cover_path` | 竖版封面（9:16，2160×3840 或等比） |
| `--horizontal-cover` | ✅ | `horizontal_cover_path` | 横版封面（4:3，1600×1200 或等比） |
| `--pack` | ❌ | 整个发布包 | Douyin Publish Pack 路径（md/json） |
| `--music` | ❌ | `music` | 背景音乐关键词，默认 `热门` |
| `--visibility` | ❌ | `visibility` | 可见性：`private` / `public`，默认 `private` |
| `--topics` | ❌ | 从 `description` 提取 | 话题标签列表；通常可直接从 description 提取 |
| `--review-timeout` | ❌ | - | 审核等待超时，默认 30 分钟 |

## 输入前置要求（来自 wemedia Step 6 发布包）

在调用前应确保 Douyin Publish Pack 已准备好，至少包括：
- `title`
- `description`（末尾含 `#标签名`）
- `video_path`
- `vertical_cover_path`
- `horizontal_cover_path`
- `music`（默认 `热门`）
- `visibility`（默认 `private`）
- 晨星确认已完成

若以上条件不满足，应回到 wemedia Step 6 或 Step 7，而不是强行执行发布。

## 标签格式（关键）

**`#标签名` 必须写在 `--description` 正文末尾**，脚本会自动提取并填入抖音话题选择器。

```bash
--description "这是我测试视频的描述 #科技 #数码 #AI"
```

格式要求：
- `#` 后不能有空格
- 每个标签单独一个 `#标签名`
- 标签紧跟正文后面，末尾可有换行
- 建议 3-8 个，最多不超过 10 个

## 配图规格

抖音需要**双封面**：

| 类型 | 比例 | 推荐尺寸 | 说明 |
|------|------|----------|------|
| 竖版封面 | 9:16 | 2160×3840px | 用于短视频 Feed 流 |
| 横版封面 | 4:3 | 1600×1200px | 用于横版推荐位 |

生成逻辑：
1. Step 5 由 NotebookLM 生成封面源图 / infographic
2. Step 6 用 `convert_cover.py` 转换为 9:16 和 4:3
3. main 在 Step 7.5 将两张封面一并传入本脚本

## 背景音乐逻辑

- 默认传 `热门`
- 若 wemedia Step 6 给出明确音乐建议，可替换为具体关键词
- 选择标准：不抢口播、不破坏信息密度、与内容气质一致

## CDP 连接参数

- **Chrome CDP 端口**: 当前脚本依赖同目录 `cdp_client.py` 的配置
- **上传页面**: `https://creator.douyin.com/creator-micro/content/upload`
- **上传按钮 targetId**: `A4299633A5F2012BB383C76D80275208`

## 工作流

### 标准工作流（wemedia 流水线 Step 7.5）
1. main 校验：晨星已确认、视频和双封面已就绪
2. 先做发布前去重检查（远端同标题 + 本地账本 content_id/video_path/title 任一命中则阻断）
3. 打开上传页面
4. 上传视频文件（若已在草稿发布页则自动跳过）
5. 填写标题 / 描述 / 话题
6. 上传双封面
7. 选择背景音乐（可选，找不到入口则跳过）
8. 提交发布
9. 等待审核结果（通常 10-30 分钟）
10. 自动跳转作品管理页核验标题/私密状态/审核中状态
11. 将结果回报给晨星和监控链路

### 分步执行
```bash
python3 scripts/publish_douyin.py --pack /path/to/douyin-pack.md --step validate_pack
python3 scripts/publish_douyin.py --step check_duplicate --title "标题" --video /path/video.mp4
python3 scripts/publish_douyin.py --step open_page
python3 scripts/publish_douyin.py --step upload_video --video /path/video.mp4
python3 scripts/publish_douyin.py --step fill_meta --title "标题" --description "描述 #AI"
python3 scripts/publish_douyin.py --step select_covers --vertical-cover /path/v.png --horizontal-cover /path/h.png
python3 scripts/publish_douyin.py --step select_music --music "热门"
python3 scripts/publish_douyin.py --step set_visibility --visibility private
python3 scripts/publish_douyin.py --step submit
python3 scripts/publish_douyin.py --step wait_review --review-timeout 30
python3 scripts/publish_douyin.py --step verify_publish --title "标题" --visibility private
```

## 典型用例

### 用例 1：wemedia 正式发布链路
1. wemedia 已完成内容创作与 Step 6 抖音适配，并产出 Douyin Publish Pack
2. 晨星在 Step 7 已确认
3. main 校验发布包后调本 skill 执行发布
4. main 继续等待审核，并回报 `review_passed` / `review_rejected` / `review_timeout`

### 用例 2：晨星明确要求“单独发布抖音”
仅在晨星明确指定跳过常规编排时，才可独立使用；仍建议至少补齐：标题、description、双封面、音乐建议、审核回报。

## 状态码

| 状态 | 说明 |
|------|------|
| `upload_ok` | 视频上传成功 |
| `meta_ok` | 元信息填写成功 |
| `covers_ok` | 封面选择成功 |
| `music_ok` | 背景音乐选择成功 |
| `submitted` | 已提交发布 |
| `review_pending` | 等待审核 |
| `review_passed` | 审核通过 |
| `review_rejected` | 审核拒绝 |
| `review_timeout` | 审核等待超时 |

## 失败处理

- 页面 / 登录 / CDP 异常：立即返回失败，不伪装成功
- 发布包缺字段：停止执行，回到 wemedia Step 6 补齐
- 封面缺失：优先使用 **CDP `DOM.setFileInputFiles`** 对封面弹层中的 hidden image input 直写文件；若仍失败，再回到 Step 6 补齐
- 审核拒绝：回传给 main，由 main 决定退回 Step 6 或 Step 4
- 审核超时：通知 main 标记 `review_timeout`，建议人工复核后台

## 已知差异（vs 小红书）

- 抖音有**内容审核**机制，提交后需等待审核结果（通常 10-30 分钟）
- 抖音**必须双封面**（竖版+横版），小红书只需单图
- 抖音有**背景音乐**选择步骤，小红书无此步骤
- 抖音话题标签系统与小红书不同，需按 `#标签名` 格式
- 本 skill 在 wemedia 流水线中的定位是**发布执行工具**，不是创作工具