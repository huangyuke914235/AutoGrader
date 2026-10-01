# bugfix：填了 API Key 却报「DEMO_MODE=true：已阻止调用真实模型」

**发现时间**：2026-09-24
**现象来源**：用户实际使用截图
**严重程度**：高（学生端「评阅」链路在配置正确的情况下完全不可用）

## 一、现象

侧边栏：

- 通道选了「DeepSeek 深度求索」
- API Key 填了，界面绿字回显 `✅ 已就绪：DeepSeek 深度求索 · 模型 deepseek-chat · Key sk-****g4f9`
- 上传报告解析成功（`已载入 UP-5a8f1427：17205 字 / 43 章节`）

点「② 开始评阅」，红字报错：

```
RuntimeError: DEMO_MODE=true：已阻止调用真实模型。如需真实评阅请把 DEMO_MODE 设为 false 并配置密钥。
```

在另一种状态下（没有预置演示结果时）报的是：

```
RuntimeError: DEMO_MODE=true 但没有可用演示结果（先跑 tools/make_demo.py）
```

两条报错都指向 `DEMO_MODE` —— 一个用户刚刚完全没有碰过、也不该需要理解的开关。

## 二、根因

一句话：**侧边栏填的密钥从来没有被传下去，评阅链路只认环境变量。**

具体是两处判断各自成立、合起来错：

1. `llm.call_json` 里写着一条正确的纪律：

   ```python
   # 学生**显式自带**的密钥属于他自己的账户，应当放行
   if demo_mode() and api_key is None:
       raise RuntimeError("DEMO_MODE=true：已阻止调用真实模型。...")
   ```

2. 但 `pipeline.py` 里 R / A / A-重试 / G / E 五处 `call_json(...)` **全都不传 `api_key`**：

   ```python
   rubric = call_json(prompts.S1_RUBRIC, user, Rubric)          # 没传
   j = call_json(prompts.S2_JUDGE, user, ItemJudgement)         # 没传
   j2 = call_json(prompts.S2_JUDGE, retry_user, ItemJudgement)  # 没传
   j2 = call_json(prompts.S3_RECHECK, user, ItemJudgement, ...) # 没传
   return call_json(prompts.S4_FEEDBACK, user, Feedback)        # 没传
   ```

   于是 `call_json` 看到的永远是 `api_key is None`，那条放行纪律**从来没有机会生效**。

3. 更前面还有一道：`run_grading` 开头就是

   ```python
   if demo_mode():
       ...
       raise RuntimeError(...)   # 连模型层都没走到
   ```

所以「学生自带密钥」这条通路从侧边栏到模型调用，中间是断的 —— 断了两处。

这个 bug 之所以难被自己发现：**两侧单独看都是对的**。选择器自己测过、`call_json` 的放行逻辑也测过，只有「串起来」才暴露。

## 三、影响面

- 评阅链路（R/A/G/E）在 `DEMO_MODE=true` 部署下，**无论学生怎么配都用不了**。
- 即使把 `DEMO_MODE` 关掉能用，学生填在界面上的 key 也**不生效**，实际用的是服务端 `.env` 里的平台密钥 —— 界面在骗人，账单在走平台。
- 学生自检链路（`run_selfcheck`）之前是单独接的 `llm_cfg`，所以它一直正常 —— 这也解释了为什么「体检能用、评阅不能用」。

## 四、修复

### 1. 收敛成唯一一处翻译（`providers.llm_kwargs`）

```python
def llm_kwargs(cfg: dict) -> dict:
    if not cfg or cfg.get("id") == "offline":
        return {}
    if validate(cfg):
        return {}
    return {"api_key": ..., "base_url": ..., "model": ...}
```

调用方不再各自识别 `offline`、各自拼字典 —— 一处判断只写一遍，杜绝「漏接一处」再次发生。

### 2. 五处调用点全部接上

`stage_rubric` / `stage_judge` / `stage_consistency` / `stage_feedback` / `run_grading` 各加一个 `llm_cfg=None` 参数（带默认值，CLI 与批量脚本不受影响），并在每次 `call_json` 上展开 `**providers.llm_kwargs(llm_cfg)`。

### 3. 把「显式自带密钥」写进主流程的放行条件

```python
kw = providers.llm_kwargs(llm_cfg)
...
if demo_mode() and not kw:      # 原来是 if demo_mode():
    ...
```

`DEMO_MODE` 拦的是「用平台配置去烧钱」；学生**显式**自带的密钥不属于这个范畴。判断纪律与 `llm.call_json` 内保持字面一致。

### 4. 顺带修掉两个同源问题

| 问题 | 修法 |
|---|---|
| 选「仅离线规则」通道跑评阅，会**静默退化**成读环境变量（用户完全不知情） | 显式拒绝并说明：离线通道只覆盖自检的 8 项规则，评阅链路必须有模型。注意与 `llm_cfg=None`（CLI 场景）区分 |
| 导出/运行信息里的 `model`、`base_url` 一律记环境变量那套，学生自带密钥跑出的结果指向错误对象 | 改记**本次真正使用**的模型与接口 |
| 侧边栏「已就绪」+ 上一行「DEMO_MODE 不会调用真实模型」组合起来误导 | 状态行改为描述**接下来真的会发生什么**，四个分支各说一句 |

## 五、验证

- `tests/test_llm_channel_passthrough.py`（新增 10 条）：
  - `llm_kwargs` 对 None / offline / 残缺配置都返回 `{}`；对有效配置原样透传三个字段
  - **核心回归**：`DEMO_MODE=true` + 学生自带 key → 必须真的走到 `call_json`，且每处都收到 key
  - **拦截力保留**：无 key 时 `DEMO_MODE` 照旧拦截、照旧返回预置演示结果
  - `llm_cfg=None` 仍走环境变量（CLI / 批量脚本行为不变）
  - 离线通道跑评阅的报错里必须出现「学生自检」这个去处
- `tools/check_grading_channel.py`（新增，端到端）：
  用「自定义」通道指向一个**故意打不通**的本地端口 `http://127.0.0.1:1/v1`。
  修复前会立刻抛 `DEMO_MODE` 的 `RuntimeError`（根本没走到网络）；
  修复后真的发起网络调用，报的是调用失败。**不需要真 key、不联网，也能精确区分修复前后**，并同时断言侧边栏提示已说真话。
- 全量回归：`pytest tests/ -q` → 131 passed, 7 skipped；`smoke_providers_ui.py`、`check_profiles.py` 全通过。

## 六、教训

1. **「界面显示已就绪」和「真的会用它」是两件事**，必须分别验证。前者只是配置合法，后者要求参数一路传到底。
2. **默认值会掩盖断路**。`llm_cfg=None` 时退回环境变量在 CLI 场景是对的，但它同时让「忘传参数」看起来像正常工作 —— 所以要用 spy 测试断言参数**真的到了**调用层，而不是只断言「没报错」。
3. **报错要说用户听得懂的话**。`DEMO_MODE=true` 对学生没有任何意义；修复后的报错直接给出两条可执行路径（去侧边栏填 key / 去改服务端变量）。
4. 同一条纪律写在两处（`call_json` 与 `run_grading`），改的时候必须两处一起改 —— 这次就是只改了模型层、漏了主流程层。
