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
    python3 mcd_mcp.py persona                 # 麦麦人格报告(生成可传播的人格卡片)
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
    """上岗建档: 全量订单(近一年)聚合统计 + 组件级深钻, 生成画像卡与画像库md."""
    from collections import Counter

    raw = call_tool("order-list")
    obj = _extract_json(raw)
    if not obj:
        print("历史订单解析失败")
        return
    import datetime as _dt
    all_orders = obj.get("data", {}).get("list") or []
    _year_ago = (_dt.date.today() - _dt.timedelta(days=365)).isoformat()
    orders = [o for o in all_orders if str(o.get("createTime", ""))[:10] >= _year_ago] or all_orders

    stores, dishes, scenes, slots, amounts = Counter(), Counter(), Counter(), Counter(), []
    for o in orders:
        scenes["到店" if str(o.get("orderType")) == "1" else "外送"] += 1
        stores[f"{o.get('storeName','?')}({o.get('storeCode','')})"] += 1
        try:
            amounts.append(float(o.get("realTotalAmount", 0)))
        except (TypeError, ValueError):
            pass
        try:
            hh = int(str(o.get("createTime", "12:00"))[11:13])
            slots["早餐" if 5 <= hh < 10.5 else ("午餐" if 11 <= hh < 14 else ("下午茶" if 14 <= hh < 17 else "晚餐"))] += 1
        except ValueError:
            pass
        for p in o.get("orderProductList", []):
            dishes[p.get("productName", "?")] += p.get("quantity", 1)

    n = len(orders)
    avg = sum(amounts) / len(amounts) if amounts else 0
    dd = _deep_dive(orders)

    # ---- 铁律习惯 vs 可变位: 频次悬殊=确定性 ----
    def _iron(counter):
        """频次>=3 且 >= 第二名1.5倍 → 铁律项(不变的习惯)."""
        if not counter:
            return None
        (top, c1), (_, c2) = counter.most_common(1)[0], (counter.most_common(2) + [("", 0)])[1]
        return (top, c1) if c1 >= 3 and c1 >= 1.5 * c2 else None

    irons, varies = [], []
    for dim_name, counter in (("主食", dd["burger"]), ("饮品位", dd["drink_slot"]),
                               ("小食位", dd["side_slot"]), ("招牌组合", dd["combo_pair"])):
        hit = _iron(counter)
        if hit:
            irons.append((dim_name, hit[0], hit[1]))
        elif counter:
            varies.append((dim_name, ", ".join(f"{k}×{v}" for k, v in counter.most_common(2))))

    # ---- 推荐规则: 深钻结果翻译成阿姨的下单指令 ----
    rules = []
    if irons:
        rules.append("铁律(直接当默认,不问): " + "; ".join(f"{d}={t}" for d, t, _ in irons))
    if varies:
        rules.append("可变位(给新意思): " + "; ".join(f"{d}({v})" for d, v in varies))
    if dd["burger_total"]:
        if dd["spicy"] == 0:
            rules.append("辣度: 只推不辣款;辣品仅用户主动要")
        elif dd["spicy"] >= dd["burger_total"] / 2:
            rules.append("辣度: 可推辣款主力")
        if dd["double_layer"] >= dd["burger_total"] * 0.6:
            rules.append("结构: 默认推双层系")
    if dd["drink_slot"]:
        top_d = dd["drink_slot"].most_common(1)[0][0]
        if "甜品位" not in top_d:
            rules.append(f"饮品位: 默认 {top_d}")
    if dd["order_shapes"]:
        top_shape = dd["order_shapes"].most_common(1)[0][0]
        if top_shape == "双套单":
            rules.append("单型: 默认问'今天也是两份?'")
        elif top_shape == "多人单":
            rules.append("单型: 默认问'今天几位?'")
    if dd["combo_pair"]:
        rules.append(f"老样子默认单: {dd['combo_pair'].most_common(1)[0][0]}")
        tried = set(dd["combo_pair"])
        rules.append(f"新意思候选: 未解锁组合优先")

    # ---- 打印画像卡 ----
    L = ["=" * 46, "    麦麦画像卡 · 麦麦阿姨上岗建档", "=" * 46,
         f"\n📜 近一年 {n} 笔订单\n"]
    L.append("🏠 常用门店: " + ", ".join(f"{s} {c}次" for s, c in stores.most_common(2)))
    L.append("🚶 场景: " + ", ".join(f"{k} {v}" for k, v in scenes.most_common()))
    if slots:
        L.append("⏰ 时段: " + ", ".join(f"{k} {c}次" for k, c in slots.most_common()))
    if dd["burger"]:
        L.append("🍔 主食: " + "、".join(f"{b}×{c}" for b, c in dd["burger"].most_common(4)))
        L.append(f"   结构 {dd['double_layer']}/{dd['burger_total']} 双层系 · " + ("不辣党" if dd["spicy"] == 0 else f"辣 {dd['spicy']} 次"))
    if dd["drink_slot"]:
        L.append("🥤 饮品位: " + "、".join(f"{k}×{v}" for k, v in dd["drink_slot"].most_common(3)))
    if dd["side_slot"]:
        L.append("🍟 小食位: " + "、".join(f"{k}×{v}" for k, v in dd["side_slot"].most_common(3)))
    if dd["combo_pair"]:
        L.append("🧩 招牌组合: " + "、".join(f"{p} ×{c}" for p, c in dd["combo_pair"].most_common(3)))
    if dd["order_shapes"]:
        L.append("👥 单型: " + "、".join(f"{k}×{v}" for k, v in dd["order_shapes"].most_common()))
    if amounts:
        L.append(f"💰 消费带: 均单 ¥{avg:.1f}（{min(amounts):.0f}–{max(amounts):.0f}）⚠️ 不跨带推荐")
    # 全菜单坐标系打点(实时拉取)
    try:
        cs = _menu_coordinate_system()
        world, cs_iron, cs_rotate, cs_blank = _dot_on_system(cs, dd, dict(slots))
        L.append(f"\n🗺️ 全菜单坐标(主世界:{world}, 实时{sum(len(v) for v in cs[world].values())}格)")
        if cs_iron:
            L.append("   铁律格: " + "、".join(f"{n}×{c}" for _, n, c in cs_iron[:4]))
        L.append(f"   点过 {len(cs_iron) + len(cs_rotate)} 格 · 未探索 {len(cs_blank)} 格")
        _explore = [f"{it['name']}" for sn, it in [(b[0], b[2]) for b in cs_blank[:0]]]  # 占位
        # 探索建议按画像属性过滤: 不辣党不推辣品,双层党优先双层
        _no_spicy = dd["spicy"] == 0 and dd["burger_total"] > 0
        _prefer_double = dd["burger_total"] and dd["double_layer"] >= dd["burger_total"] * 0.6
        def _explorable(it):
            if _no_spicy and it.get("spicy"):
                return False
            return True
        cand = [b[2] for b in cs_blank if b[0] == "主食" and _explorable(b[2])]
        cand.sort(key=lambda it: (not (_prefer_double and it.get("layer") == "双层"),))
        explore_names = [it["name"] for it in cand[:3]]
        if explore_names:
            L.append("   探索建议(空白主食): " + "、".join(explore_names))
    except Exception as e:
        L.append(f"\n🗺️ 坐标系拉取失败({e}),跳过")
    if irons:
        L.append("\n🔒 不变的习惯(铁律)")
        for d, t, c in irons:
            L.append(f"   {d}: {t} ×{c}")
    if varies:
        L.append("🔄 爱换的地方(给新意思)")
        for d, v in varies:
            L.append(f"   {d}: {v}")
    if rules:
        L.append("\n📌 阿姨推荐备忘(画像驱动)")
        for r in rules:
            L.append(f"   · {r}")
    L.append("\n" + "=" * 46)
    print("\n".join(L))

    # ---- 保存画像库 md ----
    if save:
        home = os.path.expanduser("~/.mcd-auntie")
        os.makedirs(home, exist_ok=True)
        path = os.path.join(home, "profile.md")
        keep = ""
        if os.path.exists(path):
            old = open(path, encoding="utf-8").read()
            for sec in ("## 稳定偏好", "## 候选偏好"):
                i = old.find(sec)
                if i >= 0:
                    j = old.find("\n## ", i + 4)
                    keep += old[i:j if j > 0 else len(old)] + "\n\n"
        today = _dt.date.today().isoformat()
        fallback_pref = ("## 稳定偏好（已确认）\n- （暂无——确认后从候选区挪入）\n\n"
                         "## 候选偏好（待印证：单次选择≠长期偏好）\n"
                         '- （用户口述的偏好先落这里；重复出现或用户说「以后都」，经阿姨口头确认后挪入上方稳定区）\n\n')
        pref_sections = keep or fallback_pref
        md = f"""# 麦麦阿姨的用户画像

> 此文件是阿姨对用户的认知档案。用户可随时打开修改；阿姨改动前先说一声。
> 最后刷新: {today}（近一年 {n} 笔订单, 组件级深钻）

## 统计画像（订单派生，每次点餐后刷新替换）
- 常用门店: {stores.most_common(1)[0][0] if stores else '—'}（{stores.most_common(1)[0][1] if stores else 0}/{n} 次）
- 场景/时段: {", ".join(f"{k}{v}" for k, v in scenes.most_common())} · {", ".join(f"{k}{v}次" for k, v in slots.most_common())}
- 消费带: 均单 ¥{avg:.1f}（{min(amounts):.0f}–{max(amounts):.0f}）← 推荐红线

## 深钻画像（组件级·每次点餐后刷新）
- 主食: {"、".join(f"{b}×{c}" for b, c in dd['burger'].most_common(5)) or '—'}
- 结构: 双层系 {dd['double_layer']}/{dd['burger_total']} · 辣度: {"不辣党" if dd['spicy'] == 0 else f"辣{dd['spicy']}次"}
- 饮品位: {"、".join(f"{k}×{v}" for k, v in dd['drink_slot'].most_common(4)) or '—'}
- 小食位: {"、".join(f"{k}×{v}" for k, v in dd['side_slot'].most_common(4)) or '—'}
- 招牌组合: {"、".join(f"{p}×{c}" for p, c in dd['combo_pair'].most_common(3)) or '—'}
- 单型: {"、".join(f"{k}×{v}" for k, v in dd['order_shapes'].most_common()) or '—'}

## 不变的习惯（铁律——推荐时直接当默认，不问）
{chr(10).join(f"- {d}: {t} ×{c}" for d, t, c in irons) or "- （暂无足够订单判定）"}

## 爱换的地方（可变——推荐时给新意思，别硬推重复的）
{chr(10).join(f"- {d}: {v}" for d, v in varies) or "- （暂无）"}

## 推荐规则（阿姨下单指令·画像驱动·推荐前必读）
{chr(10).join('- ' + r for r in rules) or '- （订单不足,暂无规则）'}

{pref_sections}## 变更记录
- {today} 刷新画像（近一年 {n} 单, 组件级深钻 + 推荐规则）
"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"\n💾 画像库已更新: {path}")

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
    elif cmd == "persona":
        cmd_persona()
    else:
        print(__doc__)




# ==================== 麦麦人格报告 ====================

def _food_attrs(name):
    """从餐品名推断属性: (位次, 肉类, 辣, 层数)."""
    slot = "主食"
    if any(k in name for k in ("咖", "可乐", "雪碧", "汽水", "拿铁", "茶", "汁", "美式", "摩卡")) \
            and not any(k in name for k in ("鸡", "堡", "排")):
        slot = "饮品"
    elif any(k in name for k in ("新地", "麦旋风", "奶昔", "派")):
        slot = "甜品"
    elif not any(k in name for k in ("堡", "汉堡", "麦香鱼", "巨无霸")):
        slot = "小食"
    meat = ""
    if any(k in name for k in ("鸡", "麦辣", "板烧")):
        meat = "鸡肉"
    elif any(k in name for k in ("牛", "吉士", "巨无霸", "安格斯")):
        meat = "牛肉"
    elif "鱼" in name:
        meat = "鱼肉"
    elif any(k in name for k in ("猪柳", "麦满分", "松饼", "蛋")):
        meat = "猪肉蛋"
    spicy = any(k in name for k in ("辣", "韩式", "麻辣"))
    layer = "双层" if any(k in name for k in ("双层", "巨无霸", "不素之霸", "双牛")) else ("单层" if slot == "主食" else "")
    return slot, meat, spicy, layer


def _menu_coordinate_system(store_code="1950564"):
    """双坐标系: 实时拉早餐+正餐全菜单, 输出属性标注的完整格子."""
    import datetime as _dt
    tomorrow = (_dt.date.today() + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    menus = {}
    for label, args in (("正餐", {"orderType": 1, "beType": 1, "storeCode": store_code,
                                   "reservationDate": tomorrow + " 12:00"}),
                        ("早餐", {"orderType": 1, "beType": 1, "storeCode": store_code,
                                   "reservationDate": tomorrow + " 08:00"})):
        obj = _extract_json(call_tool("query-meals", args)) or {}
        data = obj.get("data", {})
        meals = data.get("meals", {}) if isinstance(data.get("meals"), dict) else {}
        menus[label] = meals
    system = {}
    for label, meals in menus.items():
        slots = {"主食": [], "饮品": [], "甜品": [], "小食": []}
        for code, d in (meals or {}).items():
            nm = d.get("name") or code
            if not nm or nm in ("韩式烟熏芝士风味酱", "日式鲜磨山葵酱"):
                continue
            slot, meat, spicy, layer = _food_attrs(nm)
            slots[slot].append({"name": nm, "meat": meat, "spicy": spicy, "layer": layer,
                                "price": d.get("currentPrice")})
        system[label] = slots
    return system


def _deep_dive(orders):
    """组件级深钻: 每一口食物打细粒度标签(结构/肉类/辣度/饮品位/小食位/组合/单型)."""
    from collections import Counter
    burger, drink_slot, side_slot, combo_pair = Counter(), Counter(), Counter(), Counter()
    spicy_n = layer = 0
    burger_total = 0
    order_shapes = Counter()
    for o in orders:
        mains, pairs_of_order = [], []
        n_items = 0
        for p in o.get("orderProductList", []):
            n_items += p.get("quantity", 1)
            # 主品名(套餐容器或单品)
            top = p.get("productName", "")
            comps = [c.get("name", "") for c in p.get("comboItemList", [])]
            all_names = ([top] if "随心配" not in top else []) + comps
            for nm in all_names:
                is_burger = any(k in nm for k in ("堡", "汉堡", "巨无霸", "麦香鱼")) and "麦满分" not in nm
                if is_burger:
                    burger_total += 1
                    burger[nm] += 1
                    if "辣" in nm:
                        spicy_n += 1
                    if "双层" in nm or "巨无霸" in nm or "双牛" in nm or "不素之霸" in nm:
                        layer += 1
                    mains.append(nm)
                elif any(k in nm for k in ("咖", "可乐", "雪碧", "汽水", "拿铁", "茶", "汁", "美式", "摩卡")):
                    drink_slot[nm] += 1
                elif any(k in nm for k in ("新地", "麦旋风", "奶昔")):
                    drink_slot[nm + "(甜品位)"] += 1
                else:
                    side_slot[nm] += 1
            # 随心配组合对
            if "随心配" in top and len(comps) == 2:
                m = next((c for c in comps if any(k in c for k in ("堡", "汉堡"))), None)
                oth = next((c for c in comps if c != m), None)
                if m and oth:
                    combo_pair[f"{m}+{oth}"] += 1
                    pairs_of_order.append((m, oth))
        # 单型: 1件=纯单人 / 2件=双套 / >=3件=多人
        order_shapes["纯单人单" if n_items == 1 else ("双套单" if n_items == 2 else "多人单")] += 1
    return {
        "burger": burger, "burger_total": burger_total,
        "spicy": spicy_n, "double_layer": layer,
        "drink_slot": drink_slot, "side_slot": side_slot,
        "combo_pair": combo_pair, "order_shapes": order_shapes,
    }


def _dot_on_system(cs, dd, slots_stat):
    """个人订单在全菜单坐标系上打点: 主世界/铁律格/轮换格/空白探索格."""
    # 主世界: 时段分布定坐标系
    if not slots_stat:
        world = "正餐"
    else:
        top = max(slots_stat, key=slots_stat.get)
        world = "早餐" if top == "早餐" else "正餐"
    sys_slots = cs.get(world, {})
    # 打点: 点过的格子
    eaten = {}
    for counter in (dd["burger"], dd["drink_slot"], dd["side_slot"]):
        for name, cnt in counter.items():
            base = name.replace("(甜品位)", "")
            eaten[base] = max(eaten.get(base, 0), cnt)
    # 全量格子分类
    iron, rotate, blank = [], [], []
    for slot_name, items in sys_slots.items():
        for it in items:
            nm = it["name"]
            hit = next((v for k, v in eaten.items() if k in nm or nm in k), 0)
            if hit >= 3:
                iron.append((slot_name, nm, hit))
            elif hit > 0:
                rotate.append((slot_name, nm, hit))
            else:
                blank.append((slot_name, nm, it))
    return world, sorted(iron, key=lambda x: -x[2]), rotate, blank


def _persona_title(p, dishes=None):
    """从画像数据判定麦麦人格称号. dishes=单品频次表, 用于行为实锤."""
    cats = p.get("categories", {})
    slots = p.get("slots", {})
    avg = p.get("avgAmount", 0)
    tw = p.get("takeWays", {})
    # 品类派系
    if cats:
        top_cat = max(cats, key=cats.get)
        total = sum(cats.values())
        if cats[top_cat] / max(total, 1) < 0.45:
            school = "杂食均衡派"
        else:
            school = {"牛肉": "牛肉派", "鸡肉": "鸡肉派", "鱼肉": "鱼肉派"}.get(top_cat, "百味通吃派")
    else:
        school = "神秘食客"
    # 时段属性
    if slots:
        top_slot = max(slots, key=slots.get)
        period = {"早餐": "晨光麦客", "午餐": "正餐主力", "下午茶": "悠闲茶点派", "晚餐": "夜麦星人"}.get(top_slot, "随机干饭人")
    else:
        period = "随机干饭人"
    # 消费风格: 行为实锤(点了什么)优先于金额推断(花了多少)
    total_items = sum(dishes.values()) if dishes else 0
    swxp = sum(c for name, c in (dishes or {}).items() if "随心配" in name)
    if (avg < 20 or (total_items and swxp / total_items >= 0.4)) and swxp > 0:
        style = "随心配精算师"      # 真点过随心配的省钱派(金额低或占比高皆实锤)
    elif avg < 20:
        style = "精打细算型"        # 每单都低但没点随心配,不硬蹭名字
    elif avg <= 40:
        style = "稳定发挥型"
    else:
        style = "全都要星人"
    # 取餐风格
    if tw:
        top_tw = max(tw, key=tw.get)
        way = {"外带": "打包大师", "堂食": "堂食氛围组", "外送": "宅家干饭人"}.get(top_tw, "")
    else:
        way = ""
    return school, period, style, way


def cmd_persona(save=False):
    """麦麦人格报告:汇总画像与资产,生成可传播的人格卡片(SVG/PNG)."""
    import subprocess
    from collections import Counter
    raw = call_tool("order-list")
    obj = _extract_json(raw)
    all_orders = (obj or {}).get("data", {}).get("list") or []
    # 口径: 主动统计近一年内订单(不依赖 App 窗口的被动描述)
    import datetime as _dt
    _year_ago = (_dt.date.today() - _dt.timedelta(days=365)).isoformat()
    orders = [o for o in all_orders if str(o.get("createTime", ""))[:10] >= _year_ago] or all_orders
    period_label = "我的麦生"
    tenure = ""
    dates = sorted(str(o.get("createTime", ""))[:10] for o in orders if o.get("createTime"))
    if dates:
        first = _dt.date.fromisoformat(dates[0])
        months = (_dt.date.today() - first).days // 30
        tenure = f"{months // 12} 年" if months >= 12 else f"{months} 个月"
    stores, dishes, slots, categories, amounts = Counter(), Counter(), Counter(), Counter(), []
    kcal_total = 0
    for o in orders:
        stores[o.get("storeName", "?")] += 1
        try:
            amounts.append(float(o.get("realTotalAmount", 0)))
        except (TypeError, ValueError):
            pass
        try:
            hh = int(str(o.get("createTime", "12:00"))[11:13])
            slots["早餐" if 5 <= hh < 10.5 else ("午餐" if 11 <= hh < 14 else ("下午茶" if 14 <= hh < 17 else "晚餐"))] += 1
        except ValueError:
            pass
        joined_parts = []
        for prod in o.get("orderProductList", []):
            dishes[prod.get("productName", "?")] += prod.get("quantity", 1)
            parts = [prod.get("productName", "")] + [c.get("name", "") for c in prod.get("comboItemList", [])]
            joined_parts.extend(parts)
        joined = " ".join(joined_parts)
        for key, kw in (("牛肉", ("牛", "安格斯", "巨无霸", "吉士")), ("鸡肉", ("鸡", "麦辣", "板烧")), ("鱼肉", ("麦香鱼", "鱼"))):
            if any(k in joined for k in kw):
                categories[key] += 1
    n = len(orders)
    avg = sum(amounts) / len(amounts) if amounts else 0
    drink_ratio = sum(1 for o in orders if any(
        k in str(o.get("orderProductList", "")) for k in ("咖", "可乐", "汽水", "拿铁", "麦旋风", "茶", "奶昔", "雪碧", "汁")))
    # 饮品偏好: 组件级统计 + 派系
    from collections import Counter as _C
    _drinks = _C()
    for o in orders:
        for prod in o.get("orderProductList", []):
            for c in prod.get("comboItemList", []):
                nm = c.get("name", "")
                if any(k in nm for k in ("咖", "可乐", "雪碧", "汽水", "拿铁", "麦旋风", "茶", "奶昔", "汁", "美式", "摩卡")):
                    _drinks[nm] += c.get("quantity", 1)
    if _drinks:
        _dtop = _drinks.most_common(1)[0][0]
        drink_name = _dtop if len(_dtop) <= 8 else _dtop[:7] + "…"
        _dp = _drinks.most_common(1)[0][0]
        if any(k in _dp for k in ("咖", "拿铁", "美式", "摩卡")):
            drink_party = "咖啡党"
        elif any(k in _dp for k in ("麦旋风", "奶昔")):
            drink_party = "甜品派"
        elif any(k in _dp for k in ("可乐", "雪碧", "汽水")):
            drink_party = "快乐水派"
        else:
            drink_party = "茶饮派"
    else:
        drink_name, drink_party = "无饮品", "干饭纯粹派"
    # 隐私原则: 卡片不含门店位置/均单金额——用户敢晒才会晒
    p = {"categories": dict(categories), "slots": dict(slots), "avgAmount": avg,
         "takeWays": {}, "stores": {s: c for s, c in stores.most_common(3)}}
    school, period, style, way = _persona_title(p, dishes)
    top_store = stores.most_common(1)[0][0].replace("麦当劳北京", "").replace("餐厅", "") if stores else "—"
    top_dish = dishes.most_common(1)[0][0] if dishes else "—"
    # 资产侧
    acct = _extract_json(call_tool("query-my-account")) or {}
    pts = ((acct.get("data") or {}).get("availablePoint")) or "?"
    title = f"{school} · {period}"
    tags = [t for t in (school, period, style, way) if t][:3]
    print("=" * 40)
    print("      麦麦人格报告 · Auntie M")
    print("=" * 40)
    print(f"人格称号: {title}")
    print(f"标签: {' / '.join(tags)}")
    print(f"周期: {period_label} | 统计: {n} 顿 · 最爱 {top_dish} · 均单 ¥{avg:.1f}")
    print(f"家底: 积分 {pts}")
    slot_emoji = {"早餐": "☀️", "午餐": "🍔", "下午茶": "🍰", "晚餐": "🌙"}.get(max(slots, key=slots.get), "⏰") if slots else "⏰"
    # 生成卡片
    # 标签胶囊:居中横排,按数量自适应
    tag_w, tag_gap = 178, 18
    total_w = len(tags) * tag_w + (len(tags) - 1) * tag_gap
    start_x = (750 - total_w) / 2
    tag_html = "".join(
        f'<rect x="{start_x + i * (tag_w + tag_gap):.0f}" y="712" width="{tag_w}" height="58" rx="29" fill="#fdc82f"/>'
        f'<text x="{start_x + i * (tag_w + tag_gap) + tag_w / 2:.0f}" y="750" font-size="27" fill="#7A1E12" text-anchor="middle" font-weight="bold">{t}</text>'
        for i, t in enumerate(tags))
    dish_short = top_dish if len(top_dish) <= 12 else top_dish[:11] + "…"
    store_short = top_store if len(top_store) <= 12 else top_store[:11] + "…"
    cert = (" · " + ("铁粉认证" if dishes.most_common(1)[0][1] >= 5 else "真爱认证")) if dishes else ""
    tpl_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "card-template.svg")
    tpl = open(tpl_path, encoding="utf-8").read()
    svg = (tpl.replace("{{PERIOD_LABEL}}", period_label).replace("{{SCHOOL}}", school)
              .replace("{{PERIOD}}", period_label).replace("{{STYLE}}", style)
              .replace("{{SLOT_EMOJI}}", slot_emoji).replace("{{N}}", str(n))
              .replace("{{DISH}}", dish_short).replace("{{TENURE}}", tenure)
              .replace("{{DISH_COUNT}}", str(dishes.most_common(1)[0][1] if dishes else 0))
              .replace("{{SLOT}}", max(slots, key=slots.get) if slots else "神秘")
              .replace("{{DRINK}}", str(drink_ratio)).replace("{{PTS}}", str(pts))
              .replace("{{CERT}}", cert).replace("{{TAGS_SVG}}", tag_html)
              .replace("{{DRINK_NAME}}", drink_name).replace("{{DRINK_PARTY}}", drink_party)
              .replace("{{DRINK_TOP}}", str(_drinks.most_common(1)[0][1]) if _drinks else "0"))
    home = os.path.expanduser("~/.mcd-auntie")
    os.makedirs(home, exist_ok=True)
    svg_path = os.path.join(home, "persona-card.svg")
    open(svg_path, "w", encoding="utf-8").write(svg)
    print(f"\n🖼️ 卡片(SVG): {svg_path}")
    # PNG 渲染: 优先 Chrome 无头(彩色emoji/字体完整), 失败则 qlmanage 兜底
    png_path = os.path.join(home, "persona-card.png")
    html = ('<!doctype html><html><head><meta charset="utf-8"><style>'
            '*{margin:0;padding:0}html,body{width:750px;height:1334px;overflow:hidden}'
            'img{display:block;width:750px;height:1334px}</style></head>'
            '<body><img src="persona-card.svg"></body></html>')
    open(os.path.join(home, "card.html"), "w", encoding="utf-8").write(html)
    import subprocess, shutil
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if shutil.which("google-chrome") or os.path.exists(chrome):
        exe = shutil.which("google-chrome") or chrome
        subprocess.run([exe, "--headless", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=1", "--screenshot=" + png_path,
                        "--window-size=750,1334", "file://" + os.path.join(home, "card.html")],
                       capture_output=True, timeout=60)
        if os.path.exists(png_path) and os.path.getsize(png_path) > 30000:
            print(f"📱 朋友圈图(PNG): {png_path} — 直接可发")
        else:
            print("PNG 渲染未成功,请用浏览器打开 SVG 截图")
    else:
        print("未找到 Chrome,请用浏览器打开 SVG 截图")


if __name__ == "__main__":
    main()
