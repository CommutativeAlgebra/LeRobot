# openpi `Observation` 契约与 pi0.5 变换链

核对来源：openpi `main` 分支（`src/openpi/models/model.py`、`models/tokenizer.py`、`models/pi0.py`、`models/pi0_config.py`、`transforms.py`、`training/config.py`、`training/data_loader.py`）。

---

## 1. `Observation` 结构

```python
@struct.dataclass
class Observation:
    images: dict[str, Float[*b, h, w, c]]      # [-1, 1] float32
    image_masks: dict[str, Bool[*b]]           # 与 images 同键
    state: Float[*b, s]
    tokenized_prompt: Int[*b, l] | None
    tokenized_prompt_mask: Bool[*b, l] | None
    token_ar_mask: Int[*b, l] | None           # 仅 FAST 用
    token_loss_mask: Bool[*b, l] | None        # 仅 FAST 用
```

`IMAGE_KEYS` 固定为三元组，`IMAGE_RESOLUTION = (224, 224)`：

```
base_0_rgb  ← head
left_wrist_0_rgb  ← left_wrist
right_wrist_0_rgb ← right_wrist
```

`Observation.from_dict` 会把 uint8 图像自动转成 `[-1, 1]`：

```python
data["image"][k] = data["image"][k].astype(np.float32) / 255.0 * 2.0 - 1.0
```

---

## 2. 图像预处理（`preprocess_observation`）

- 尺寸不是 224 时用 `image_tools.resize_with_pad`（**保持长宽比 + 补边**，不裁剪）。
  - 640×480 → 224×168 → 上下各补 28px。4:3 采集会浪费约 25% 像素；建议采集端用 480×480。
- `train=True` 时增强：
  ```python
  if "wrist" not in key:            # 仅 base_0_rgb
      RandomCrop(95%) + Resize + Rotate(±5°)
  ColorJitter(brightness=0.3, contrast=0.4, saturation=0.5)   # 三路都做
  ```
  → 头部相机承担视角鲁棒性，wrist 保持精确。
- `image_masks` 缺省为全 `True`；显式给 `False` 时该视角被替换为全黑图。

---

## 3. pi0.5 的核心差异：state 走文字

`Pi0Config(pi05=True)` 的默认值：

| 参数 | 值 |
|---|---|
| `action_dim` | 32 |
| `action_horizon` | 50 |
| `max_token_len` | 200（pi0 为 48） |
| `discrete_state_input` | `True`（`pi05` 默认） |

分词（`PaligemmaTokenizer.tokenize`）：

```python
discretized_state = np.digitize(state, bins=np.linspace(-1, 1, 257)[:-1]) - 1
state_str = " ".join(map(str, discretized_state))
full_prompt = f"Task: {cleaned_text}, State: {state_str};\nAction: "
```

其中 `cleaned_text = prompt.strip().replace("_", " ").replace("\n", " ")`。

**致命点**：bins 固定为 `[-1, 1]`。若 state 未归一化（例如原始值 `-99.3`），`digitize` 全部落到边界 → 所有维度都是 `0` 或 `255`，**状态输入完全饱和**，且 loss 照常下降、不会报错。

另：`Pi0.embed_suffix` 中 `if not self.pi05: state_token = self.state_proj(obs.state)` —— **pi0.5 不使用 `state` 张量**，只使用 token 里的离散 state。补零到 32 维仅满足 `inputs_spec` 形状要求。

---

## 4. 变换链顺序（关键）

数据加载（`transform_dataset`）：

```python
repack_transforms.inputs
→ data_transforms.inputs        # 含 DeltaActions（绝对→delta）
→ Normalize(use_quantiles=True)
→ model_transforms.inputs       # ResizeImages(224) → TokenizePrompt → PadStatesAndActions(32)
```

要点：

1. **`DeltaActions` 在 `Normalize` 之前**：先 `a -= s`（绝对→相对），再归一化。
2. **`TokenizePrompt` 在 `PadStatesAndActions` 之前**：prompt 里只含真实维数（12 或 17 个整数），不会混入补零。
3. **`Normalize` 在 `TokenizePrompt` 之前**：这是 state 能被正确离散化成 256 档的前提。

delta mask（双臂 5+1）：

```python
delta_action_mask = _transforms.make_bool_mask(5, -1, 5, -1)   # 5关节 delta, 夹爪绝对, 5, 绝对
```

夹爪必须保持绝对（`False`）：开合是二态，做 delta 无物理意义且推理时易漂。

---

## 5. 损失

`Pi0.compute_loss` 是 **flow matching**，不是直接回归动作：

```python
noise = randn_like(actions)
t     = Beta(1.5, 1) * 0.999 + 0.001
x_t   = t * noise + (1 - t) * actions
u_t   = noise - actions        # 目标速度场
v_t   = model(x_t, t)          # 预测
loss  = mean((v_t - u_t) ** 2, axis=-1)
```

真实指令只通过 `u_t` 进入。`PadStatesAndActions` 补出的 20 个维度目标恒为 `noise`（因为是 0 补零后再加噪），属于 openpi 对低维机器人（如 ALOHA 14 维）的既有行为。

---

## 6. 推理侧逆变换（必须与训练严格互逆）

```
model (50,32) 归一化·delta
  → Unnormalize          → delta, 物理量纲
  → AbsoluteActions(mask)→ + 当前 state → 绝对关节角
  → 自定义 Outputs       → 切片到 12 维 + 组装 {"actions": ...}
  → 客户端 dispatch      → 12 键 dict
  → robot.send_action()
```

三个一致性条件（任一不满足即静默学歪）：

1. 数据集里存**绝对角**，delta 由 transform 做。
2. `observation.state` 与 `action` 必须**同帧同序**（`DeltaActions` 用同帧 state 作基准）。
3. 单位统一走总线归一化（`-100~100`），不要混用弧度。

---

## 7. 三个致命细节

| 细节 | 后果 |
|---|---|
| state 未归一化就 tokenize | 全部落到 0/255，状态输入失效，loss 仍正常下降 |
| 图像通道顺序错（多转一次 BGR→RGB） | 红蓝互换，模型学到错误颜色，指标看不出 |
| `max_token_len=200` 不够 | 动作 token 被截断，仅 warning，不报错 |
