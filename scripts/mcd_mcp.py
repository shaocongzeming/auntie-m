#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦阿姨 - 麦当劳 MCP 命令行封装.

纯 Python 标准库实现，零依赖。通过环境变量 MCD_MCP_TOKEN 认证。

用法:
    export MCD_MCP_TOKEN=你的token
    python3 mcd_mcp.py list                    # 列出全部 MCP 工具
    python3 mcd_mcp.py call query-my-account   # 调用某个工具(参数用 JSON 传)
    python3 mcd_mcp.py call query-meals '{"orderType":1}'
    python3 mcd_mcp.py report                  # 一键资产体检(只读,不动资产)
    python3 mcd_mcp.py profile [--save]        # 生成麦麦画像卡(--save 存为记忆文件)
"""

import json
import os
import sys
import urllib.request

MCP_URL = "https://mcp.mcd.cn"
REQ_ID = 100


def _post(method, params=None):
    """发一次 MCP JSON-RPC 请求,返回解析后的 result."""
    global REQ_ID
    REQ_ID += 1
    payload = {"jsonrpc": "2.0", "id": REQ_ID, "method": method}
    if params is not None:
        payload["params"] = params
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer " + os.environ.get("MCD_MCP_TOKEN", ""),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        # Streamable HTTP 可能返回 event-stream 格式,逐行找 JSON
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("id") == payload["id"]:
                if "error" in obj:
                    raise SystemExit("MCP 错误: " + json.dumps(obj["error"], ensure_ascii=False))
                return obj.get("result", {})
    raise SystemExit("未解析到响应:\n" + body[:2000])


def call_tool(name, arguments=None):
    """调用一个 MCP 工具,返回 content 里第一个 text."""
    result = _post("tools/call", {"name": name, "arguments": arguments or {}})
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    return "\n".join(texts)


def cmd_list():
    result = _post("tools/list", {})
    tools = result.get("tools", [])
    print(f"共 {len(tools)} 个工具:\n")
    for t in tools:
        desc = " ".join(t.get("description", "").split())[:60]
        print(f"  {t['name']}\n      {desc}")


def _extract_json(text):
    """从 MCP 返回文本里提取 data JSON 对象."""
    import re
    m = re.search(r"\{\"success\".*", text, re.S)
    if not m:
        return None
    raw = m.group(0)
    # 末尾可能拼了别的文本,逐字符收敛到合法 JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        dec = json.JSONDecoder()
        try:
            obj, _ = dec.raw_decode(raw)
            return obj
        except json.JSONDecodeError:
            return None


def cmd_profile(save=False):
    """上岗建档:读历史订单(+详情),生成多维麦麦画像卡(数字员工的记忆)."""
    from collections import Counter

    raw = call_tool("order-list")
    obj = _extract_json(raw)
    if not obj:
        print("历史订单解析失败")
        return
    orders = obj.get("data", {}).get("list") or obj.get("data", {}).get("orders") or []

    stores, dishes, scenes, amounts = Counter(), Counter(), Counter(), []
    slots, categories, remarks, takeways = Counter(), Counter(), [], Counter()
    combo_meals = []  # 每单的组件清单, 用于搭配习惯
    for idx, o in enumerate(orders):
        scene = "到店" if str(o.get("orderType")) == "1" else "外送"
        scenes[scene] += 1
        stores[f"{o.get('storeName','?')}({o.get('storeCode','')})"] += 1
        try:
            amounts.append(float(o.get("realTotalAmount", 0)))
        except (TypeError, ValueError):
            pass
        # 时段: 早餐 05:00-10:30
        try:
            hh = int(str(o.get("createTime", "12:00"))[11:13])
            slots["早餐" if 5 <= hh < 10.5 else ("午餐" if 11 <= hh < 14 else ("下午茶" if 14 <= hh < 17 else "晚餐"))] += 1
        except ValueError:
            pass
        meal_parts = []
        for p in o.get("orderProductList", []):
            name = p.get("productName", "?")
            dishes[name] += p.get("quantity", 1)
            parts = [name] + [c.get("name", "") for c in p.get("comboItemList", [])]
            meal_parts.extend(parts)
            joined = " ".join(parts)
            if "鸡" in joined or "麦辣" in joined or "板烧" in joined:
                categories["鸡肉"] += 1
            if "牛" in joined or "安格斯" in joined or "巨无霸" in joined or "吉士" in joined:
                categories["牛肉"] += 1
            if "麦香鱼" in joined or "鱼" in joined:
                categories["鱼肉"] += 1
        combo_meals.append(meal_parts)
        # 订单详情: 备注/取餐方式/餐具 (限流 600/min, 抽前几单足够)
        if idx < 8:
            detail = _extract_json(call_tool("query-order", {"orderId": o.get("orderId")}))
            d = (detail or {}).get("data", {})
            if d.get("remark"):
                remarks.append(str(d["remark"]).strip())
            if d.get("takeWay"):
                takeways[str(d["takeWay"])] += 1

    def drink_rate():
        has_drink = sum(1 for parts in combo_meals if any(k in " ".join(parts) for k in ("咖", "可乐", "汽水", "拿铁", "麦旋风", "汁", "茶", "奶昔", "雪碧", "水")))
        return has_drink

    n = len(orders)
    avg = sum(amounts) / len(amounts) if amounts else 0
    L = []
    L.append("=" * 46)
    L.append("    麦麦画像卡 · 麦麦阿姨上岗建档")
    L.append("=" * 46)
    L.append(f"\n📜 分析了 {n} 笔历史订单\n")
    L.append("🏠 常用门店(按频次):")
    for s, c in stores.most_common(3):
        L.append(f"   {c}次  {s}")
    L.append("\n🚶 场景习惯:")
    for s, c in scenes.most_common():
        L.append(f"   {s} {c}/{n} ({c * 100 // n}%)")
    if slots:
        L.append("\n⏰ 时段习惯:")
        for s, c in slots.most_common():
            L.append(f"   {s} {c}次")
    if categories:
        L.append("\n🍔 品类偏好:")
        for s, c in categories.most_common():
            L.append(f"   {s}类 {c}次")
    L.append(f"\n🥤 搭配习惯: {drink_rate()}/{n} 单含饮品")
    L.append("\n🍜 口味偏好(点过什么):")
    for d, c in dishes.most_common(5):
        L.append(f"   {c}次  {d}")
    if remarks:
        L.append("\n📝 备注习惯(下单时主动继承):")
        for r in dict.fromkeys(remarks):
            L.append(f"   \"{r}\"")
    if takeways:
        L.append("\n🍽️ 取餐方式: " + ", ".join(f"{k} {v}次" for k, v in takeways.most_common()))
    if amounts:
        L.append(f"\n💰 消费带: 均单 ¥{avg:.1f}  最低 ¥{min(amounts):.0f}  最高 ¥{max(amounts):.0f}")
        L.append("   ⚠️ 推荐红线: 不跨消费带推荐")
    L.append("\n" + "=" * 46)
    L.append("确认无误后, 这就是阿姨的服务默认上下文")
    print("\n".join(L))

    if save:
        home = os.path.expanduser("~/.mcd-auntie")
        os.makedirs(home, exist_ok=True)
        path = os.path.join(home, "profile.md")
        # 保留旧的候选/稳定偏好区(用户数据不因刷新丢失)
        keep = ""
        if os.path.exists(path):
            old = open(path, encoding="utf-8").read()
            for sec in ("## 稳定偏好", "## 候选偏好"):
                i = old.find(sec)
                if i >= 0:
                    j = old.find("\n## ", i + 4)
                    keep += old[i:j if j > 0 else len(old)] + "\n\n"
        today = __import__("datetime").date.today().isoformat()
        md = f"""# 麦麦阿姨的用户画像

> 此文件是阿姨对用户的认知档案。用户可随时打开修改；阿姨改动前先说一声。
> 最后刷新: {today}（{n} 笔订单分析）

## 统计画像（订单派生，每次点餐后刷新替换）
- 常用门店: {stores.most_common(1)[0][0]}（{stores.most_common(1)[0][1]}/{n} 次）
- 场景: {", ".join(f"{k} {v}" for k, v in scenes.most_common())}
- 时段: {", ".join(f"{k} {v}次" for k, v in slots.most_common())}
- 品类: {", ".join(f"{k} {v}次" for k, v in categories.most_common())}
- 搭配: 含饮品 {drink_rate()}/{n} 单
- 取餐: {", ".join(f"{k} {v}次" for k, v in takeways.most_common())}
- 消费带: 均单 ¥{avg:.1f}（{min(amounts):.0f}–{max(amounts):.0f}）← 推荐红线,不跨带推荐

{keep}## 候选偏好（待印证：单次选择≠长期偏好）
- （用户口述的偏好先落这里；重复出现或用户说"以后都"，经阿姨口头确认后挪入上方稳定区）

## 变更记录
- {today} 刷新统计画像（{n} 单）
"""
        if "## 稳定偏好" not in md:
            md = md.replace("## 候选偏好", "## 稳定偏好（已确认）\n- （暂无——确认后从候选区挪入）\n\n## 候选偏好")
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"\n💾 画像库已更新: {path} (用户数据,与技能包分离)")

def cmd_report():
    """资产体检:只串只读工具,输出麦麦资产负债表."""
    print("=" * 46)
    print("       麦麦资产负债表 · 麦麦阿姨盘家底")
    print("=" * 46)

    print("\n[1/3] 积分账户")
    try:
        print(call_tool("query-my-account"))
    except Exception as e:
        print(f"  查询失败: {e}")

    print("\n[2/3] 卡包中的券(资产)")
    try:
        print(call_tool("query-my-coupons"))
    except Exception as e:
        print(f"  查询失败: {e}")

    print("\n[3/3] 当前可领的券(待收割羊毛)")
    try:
        print(call_tool("available-coupons"))
    except Exception as e:
        print(f"  查询失败: {e}")

    print("\n" + "=" * 46)
    print("提示: 执行类操作(领券/下单/抽奖)请通过 AI 确认后")
    print("调用 auto-bind-coupons / create-order / draw-lottery")


def main():
    if not os.environ.get("MCD_MCP_TOKEN"):
        raise SystemExit("请先设置环境变量 MCD_MCP_TOKEN (在 open.mcd.cn/mcp 申请)")
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd, params = args[0], args[1:]
    if cmd == "list":
        cmd_list()
    elif cmd == "call" and params:
        arguments = json.loads(params[1]) if len(params) > 1 else {}
        print(call_tool(params[0], arguments))
    elif cmd == "report":
        cmd_report()
    elif cmd == "profile":
        cmd_profile(save="--save" in params)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
