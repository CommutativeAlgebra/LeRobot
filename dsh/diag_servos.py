#!/usr/bin/env python
"""舵机总线只读诊断：逐个 ID 探测，定位"没有应答"的那颗舵机。

完全不写任何寄存器，不改力矩状态，只读 Model_Number / 电压 / 温度 / 位置。

用法（**先停掉遥操进程**，串口是独占的）：

    P=~/miniconda3/envs/lerobot/bin/python
    $P dsh/diag_servos.py                       # 默认 ttyACM0 + ttyACM1
    $P dsh/diag_servos.py --ports /dev/ttyACM1  # 只测一条总线
    $P dsh/diag_servos.py --ids 1-9 --retries 2

判读：
    * 全部期望 ID 应答 → 通信正常，问题在别处（供电峰值/机械/时序）
    * 某 ID 起连续缺失 → 该处之前的连接线松了/断了（菊花链）
    * 单颗缺失、后面正常 → 那颗舵机本体或 ID 设置有问题
    * 电压 < 10.5V → 供电不足，先充电
    * 温度 > 60°C → 过热保护

注意：两条总线的 ID 段不同 —— bus1 只有 1~8（左臂 6 + 头部 2），
bus2 是 1~9（右臂 6 + 底盘 3）。用 --expect 可按需覆盖。
"""

from __future__ import annotations

import argparse
import sys
import time

# 默认按 XLeRobot 的接线：port1 = 左臂(1-6) + 头部(7-8)；port2 = 右臂(1-6) + 底盘(7-9)
DEFAULT_EXPECT = {
    "/dev/ttyACM0": "1-8",
    "/dev/ttyACM1": "1-9",
}

# STS3215 控制表地址（与 lerobot/motors/feetech/tables.py 一致）
ADDR_MODEL = 3
ADDR_TORQUE = 40
ADDR_POSITION = 56
ADDR_VELOCITY = 58
ADDR_LOAD = 60
ADDR_VOLTAGE = 62
ADDR_TEMPERATURE = 63


def parse_ids(spec: str) -> list[int]:
    ids: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            ids.extend(range(int(a), int(b) + 1))
        elif part:
            ids.append(int(part))
    return ids


def read_u16(ph, port, motor_id, addr):
    value, comm, _ = ph.read2ByteTxRx(port, motor_id, addr)
    return value if comm == 0 else None


def read_u8(ph, port, motor_id, addr):
    value, comm, _ = ph.read1ByteTxRx(port, motor_id, addr)
    return value if comm == 0 else None


def probe_port(scs, path: str, ids: list[int], baud: int, retries: int) -> list[dict]:
    port = scs.PortHandler(path)
    if not port.openPort():
        print(f"  ❌ 打不开 {path}（被占用 / 无权限 / 设备不存在）")
        return []
    try:
        port.setBaudRate(baud)
        ph = scs.PacketHandler(0)  # SCS 系列协议版本 0
        rows = []
        for motor_id in ids:
            model = None
            for _ in range(retries + 1):
                model, comm, _ = ph.ping(port, motor_id)
                if comm == 0:
                    break
                time.sleep(0.02)
            if model is None or comm != 0:
                rows.append({"id": motor_id, "ok": False})
                continue
            rows.append({
                "id": motor_id,
                "ok": True,
                "model": model,
                "torque": read_u8(ph, port, motor_id, ADDR_TORQUE),
                "position": read_u16(ph, port, motor_id, ADDR_POSITION),
                "velocity": read_u16(ph, port, motor_id, ADDR_VELOCITY),
                "load": read_u16(ph, port, motor_id, ADDR_LOAD),
                "voltage": read_u8(ph, port, motor_id, ADDR_VOLTAGE),
                "temperature": read_u8(ph, port, motor_id, ADDR_TEMPERATURE),
            })
        return rows
    finally:
        port.closePort()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="舵机总线只读诊断")
    ap.add_argument("--ports", default="/dev/ttyACM0,/dev/ttyACM1")
    ap.add_argument("--ids", default=None,
                    help="要探测的 ID（默认按端口取 1-8 / 1-9）")
    ap.add_argument("--expect", default=None,
                    help='期望存在的 ID，如 "1-8"; 覆盖默认映射')
    ap.add_argument("--baud", type=int, default=1_000_000)
    ap.add_argument("--retries", type=int, default=1)
    args = ap.parse_args(argv)

    try:
        import scservo_sdk as scs
    except ImportError:
        print("缺少 scservo_sdk，请用 lerobot 环境：~/miniconda3/envs/lerobot/bin/python")
        return 2

    missing_total = 0
    for path in [p.strip() for p in args.ports.split(",") if p.strip()]:
        spec = args.ids or args.expect or DEFAULT_EXPECT.get(path, "1-9")
        ids = parse_ids(spec)
        print(f"\n== {path} ==  (期望 ID: {spec})")
        rows = probe_port(scs, path, ids, args.baud, args.retries)
        if not rows:
            missing_total += len(ids)
            continue

        found = [r for r in rows if r["ok"]]
        missing = [r["id"] for r in rows if not r["ok"]]
        print(f"  应答 {len(found)}/{len(rows)} 个 ID")
        if found:
            print(f"  {'ID':>3} {'型号':>6} {'力矩':>4} {'位置':>6} {'速度':>6} {'负载':>6} {'电压':>6} {'温度':>5}")
            for r in found:
                volt = f"{r['voltage']/10:.1f}V" if r["voltage"] is not None else "?"
                temp = f"{r['temperature']}C" if r["temperature"] is not None else "?"
                print(f"  {r['id']:>3} {r['model']:>6} {str(r['torque']):>4} "
                      f"{str(r['position']):>6} {str(r['velocity']):>6} {str(r['load']):>6} "
                      f"{volt:>6} {temp:>5}")

            low = [r["id"] for r in found if (r["voltage"] or 999) < 105]
            hot = [r["id"] for r in found if (r["temperature"] or 0) > 60]
            if low:
                print(f"  ⚠️  电压偏低（<10.5V）：ID {low} —— 先充电/换电源")
            if hot:
                print(f"  ⚠️  温度偏高（>60°C）：ID {hot} —— 可能进入过热保护")

        if missing:
            missing_total += len(missing)
            print(f"  ❌ 无应答：ID {missing}")
            # 菊花链特征：从某个 ID 起连续缺失
            tail = list(range(min(missing), max(ids) + 1))
            if missing == tail:
                print(f"     ↳ 从 ID {min(missing)} 起连续缺失，更像**菊花链在该处断开**（检查前一颗与它之间的线）")
            elif len(missing) == 1:
                print("     ↳ 仅单颗缺失，更像**该舵机本体/ID 设置**问题")

    print()
    if missing_total:
        print(f"结论：共 {missing_total} 个 ID 未应答。")
        return 1
    print("结论：所有 ID 均应答，通信层没问题。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
