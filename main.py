import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime
import os
from dotenv import load_dotenv

# ================= 1. 各種設定 =================
load_dotenv()
TOKEN = os.getenv("TOKEN")

ADMIN_ROLE_ID = 1459388566760325318      
OMNIS_ROLE_ID = 1459208662055911538     
WORK_ROLE_ID = 1459209336076374068       

ADMIN_PANEL_CH = 1459371812310745171    
ITEM_PANEL_CH = 1461057553021538485     
GENERAL_PANEL_CH = 1458801073899966585   
ALERT_CH_ID = 1460745784491380799       

ROLE_OPTIONS = {
    "オムニス権限": 1459208662055911538,
    "管理者ロール": 1459388566760325318,
    "会頭ロール": 1454307785717321738,
    "交易師ロール": 1454310938017661031,
    "従業員ロール": 1455242976258297917,
    "アルバイトロール": 1455243576337502228
}

DB_PATH = "master_system_v11.db"
CURRENCY = "円"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= 2. データベース & 共通関数 =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS work_logs(user_id INTEGER, start DATETIME, end DATETIME);
        CREATE TABLE IF NOT EXISTS materials(name TEXT PRIMARY KEY, current INTEGER DEFAULT 0, threshold INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS products(name TEXT PRIMARY KEY, price INTEGER DEFAULT 0, current INTEGER DEFAULT 0, threshold INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS recipes(product_name TEXT, material_name TEXT, quantity INTEGER, PRIMARY KEY(product_name, material_name));
        CREATE TABLE IF NOT EXISTS sales_ranking(user_id INTEGER PRIMARY KEY, total_amount INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, detail TEXT, created_at DATETIME);
        """)
        await db.commit()

async def add_audit(user_id, action, detail):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO audit_logs (user_id, action, detail, created_at) VALUES (?,?,?,?)",
                        (user_id, action, detail, datetime.now()))
        await db.commit()

def format_minutes(total_minutes):
    hrs = total_minutes // 60
    mins = total_minutes % 60
    return f"{hrs}時間{mins}分"

class GenericInputModal(discord.ui.Modal):
    def __init__(self, title, label, callback_func, placeholder=None, default=None):
        super().__init__(title=title)
        self.input_field = discord.ui.TextInput(label=label, placeholder=placeholder, default=default)
        self.add_item(self.input_field); self.callback_func = callback_func
    async def on_submit(self, interaction: discord.Interaction): await self.callback_func(interaction, self.input_field.value)

# ================= 3. View 定義 =================

# --- 商品管理パネル ---
class ItemPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    async def interaction_check(self, i: discord.Interaction):
        if any(r.id == ADMIN_ROLE_ID for r in i.user.roles): return True
        await i.response.send_message("❌ 管理者専用です。", ephemeral=True); return False

    @discord.ui.button(label="📜 登録・レシピ・個別操作(削除・価格)", style=discord.ButtonStyle.primary, custom_id="v11_it_reg")
    async def reg_menu(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            p = await (await db.execute("SELECT name, price FROM products")).fetchall()
            m = await (await db.execute("SELECT name FROM materials")).fetchall()
        view = discord.ui.View()
        
        # 新規追加
        async def add_p_cb(idx, val):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR IGNORE INTO products (name) VALUES (?)", (val,)); await db.commit()
            await idx.response.send_message(f"✅ 商品 {val} を登録しました。", ephemeral=True)
        async def add_m_cb(idx, val):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR IGNORE INTO materials (name) VALUES (?)", (val,)); await db.commit()
            await idx.response.send_message(f"✅ 素材 {val} を登録しました。", ephemeral=True)
        
        btn_add_p = discord.ui.Button(label="商品登録", style=discord.ButtonStyle.success)
        btn_add_p.callback = lambda x: x.response.send_modal(GenericInputModal("新規商品", "商品名を入力", add_p_cb))
        btn_add_m = discord.ui.Button(label="素材登録", style=discord.ButtonStyle.success)
        btn_add_m.callback = lambda x: x.response.send_modal(GenericInputModal("新規素材", "素材名を入力", add_m_cb))
        view.add_item(btn_add_p).add_item(btn_add_m)

        # 個別操作 (ここに削除があります)
        if p or m:
            sel_mng = discord.ui.Select(placeholder="🛠️ 既存アイテムの操作(削除・価格)", row=1)
            for x in p: sel_mng.add_option(label=f"商品: {x[0]}", value=f"p:{x[0]}")
            for x in m: sel_mng.add_option(label=f"素材: {x[0]}", value=f"m:{x[0]}")
            
            async def mng_cb(i2):
                mode, name = sel_mng.values[0].split(":"); v3 = discord.ui.View()
                
                async def del_act(i3):
                    async with aiosqlite.connect(DB_PATH) as db:
                        if mode == "p": 
                            await db.execute("DELETE FROM products WHERE name=?", (name,))
                            await db.execute("DELETE FROM recipes WHERE product_name=?", (name,))
                        else: 
                            await db.execute("DELETE FROM materials WHERE name=?", (name,))
                            await db.execute("DELETE FROM recipes WHERE material_name=?", (name,))
                        await db.commit()
                    await i3.response.send_message(f"🗑️ {name} を削除しました。", ephemeral=True)
                
                async def prc_act(i3, val):
                    async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE products SET price=? WHERE name=?", (int(val), name)); await db.commit()
                    await i3.response.send_message(f"✅ {name} の価格を {val}{CURRENCY} に変更しました。", ephemeral=True)

                btn_del = discord.ui.Button(label="❌ 削除", style=discord.ButtonStyle.danger)
                btn_del.callback = del_act; v3.add_item(btn_del)
                
                if mode == "p":
                    btn_prc = discord.ui.Button(label="💰 価格変更", style=discord.ButtonStyle.primary)
                    btn_prc.callback = lambda x: x.response.send_modal(GenericInputModal("単価変更", "新しい価格を入力", prc_act))
                    v3.add_item(btn_prc)
                
                await i2.response.send_message(f"【{name}】に対して何を行いますか？", view=v3, ephemeral=True)
            
            sel_mng.callback = mng_cb; view.add_item(sel_mng)

        await i.response.send_message("商品管理メニュー:", view=view, ephemeral=True)

    @discord.ui.button(label="📦 在庫補充/引出", style=discord.ButtonStyle.secondary, custom_id="v11_it_stock")
    async def stock_menu(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            m = await (await db.execute("SELECT name, current FROM materials")).fetchall()
        view = discord.ui.View()
        if m:
            sel = discord.ui.Select(placeholder="素材を選択")
            for x in m: sel.add_option(label=f"{x[0]} (在庫:{x[1]})", value=x[0])
            async def adj_cb(i2, val):
                v = int(val)
                async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE materials SET current = current + ? WHERE name=?", (v, sel.values[0])); await db.commit()
                await i2.response.send_message(f"✅ {sel.values[0]} を {v} 調整しました。", ephemeral=True)
            sel.callback = lambda i2: i2.response.send_modal(GenericInputModal("調整", "数 (+補充 / -引出)", adj_cb))
            view.add_item(sel)
        await i.response.send_message("在庫調整:", view=view, ephemeral=True)

# --- 管理者パネル ---
class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    async def interaction_check(self, i: discord.Interaction):
        if any(r.id == ADMIN_ROLE_ID for r in i.user.roles): return True
        await i.response.send_message("❌ 管理者権限不足", ephemeral=True); return False

    @discord.ui.button(label="👤 メンバー管理", style=discord.ButtonStyle.success, custom_id="v11_ad_mem")
    async def member(self, i, b):
        view = discord.ui.View(); sel = discord.ui.Select(placeholder="ロール選択")
        for n, rid in ROLE_OPTIONS.items(): sel.add_option(label=n, value=str(rid))
        async def m_cb(i2):
            rid = int(sel.values[0])
            async def role_act(i3, uid, act):
                try:
                    t = i3.guild.get_member(int(uid)); r = i3.guild.get_role(rid)
                    if act == "add": await t.add_roles(r)
                    else: await t.remove_roles(r)
                    await i3.response.send_message("✅ 完了", ephemeral=True)
                except: await i3.response.send_message("❌ エラー", ephemeral=True)
            v2 = discord.ui.View()
            v2.add_item(discord.ui.Button(label="付与", style=discord.ButtonStyle.primary)).callback = lambda x: x.response.send_modal(GenericInputModal("付与", "ID", lambda i4, v: role_act(i4, v, "add")))
            v2.add_item(discord.ui.Button(label="剥奪", style=discord.ButtonStyle.danger)).callback = lambda x: x.response.send_modal(GenericInputModal("剥奪", "ID", lambda i4, v: role_act(i4, v, "rem")))
            await i2.response.send_message("操作:", view=v2, ephemeral=True)
        sel.callback = m_cb; view.add_item(sel); await i.response.send_message("管理:", view=view, ephemeral=True)

    @discord.ui.button(label="🏆 統計/リセット", style=discord.ButtonStyle.gray, custom_id="v11_ad_stat")
    async def stats(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            rank = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
            work = await (await db.execute("SELECT user_id, SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE end IS NOT NULL GROUP BY user_id")).fetchall()
        
        txt = "🏆 **売上ランキング**\n" + "\n".join([f"<@{r[0]}>: {r[1]:,}{CURRENCY}" for r in rank]) if rank else "データなし"
        txt += "\n\n📊 **累計勤務時間**\n" + "\n".join([f"<@{w[0]}>: `{format_minutes(int(w[1]//60))}`" for w in work]) if work else "データなし"
        
        view = discord.ui.View()
        async def confirm_reset(i_res, sql, msg):
            v_conf = discord.ui.View()
            btn = discord.ui.Button(label="本当に実行する", style=discord.ButtonStyle.danger)
            async def exec_reset(i_exec):
                async with aiosqlite.connect(DB_PATH) as db: await db.execute(sql); await db.commit()
                await i_exec.response.send_message(f"✅ {msg}", ephemeral=True)
            btn.callback = exec_reset; v_conf.add_item(btn)
            await i_res.response.send_message("⚠️ リセットを実行しますか？", view=v_conf, ephemeral=True)

        async def reset_ind(i_ind, uid):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE sales_ranking SET total_amount = 0 WHERE user_id = ?", (int(uid),)); await db.commit()
            await i_ind.response.send_message(f"✅ <@{uid}> の売上を0にしました。", ephemeral=True)

        view.add_item(discord.ui.Button(label="個人売上リセット", style=discord.ButtonStyle.secondary)).callback = lambda x: x.response.send_modal(GenericInputModal("個人リセット", "IDを入力", reset_ind))
        view.add_item(discord.ui.Button(label="全体売上リセット", style=discord.ButtonStyle.danger)).callback = lambda x: confirm_reset(x, "DELETE FROM sales_ranking", "全員の売上を削除しました。")
        view.add_item(discord.ui.Button(label="勤務記録リセット", style=discord.ButtonStyle.danger)).callback = lambda x: confirm_reset(x, "DELETE FROM work_logs", "勤務記録を削除しました。")
        await i.response.send_message(txt, view=view, ephemeral=True)

    @discord.ui.button(label="📜 履歴ログ", style=discord.ButtonStyle.gray, custom_id="v11_ad_log")
    async def logs(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, user_id, action, detail FROM audit_logs ORDER BY id DESC LIMIT 15")).fetchall()
        txt = "📜 **操作履歴**\n" + "\n".join([f"`{r[0][5:16]}` <@{r[1]}> **{r[2]}**: {r[3]}" for r in rows]) if rows else "なし"
        await i.response.send_message(txt, ephemeral=True)

# --- 業務パネル ---
class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    async def check_active_work(self, i: discord.Interaction):
        if not any(r.id == OMNIS_ROLE_ID for r in i.user.roles):
            await i.response.send_message("❌ オムニス商会ロールが必要です。", ephemeral=True); return False
        if not any(r.id == WORK_ROLE_ID for r in i.user.roles):
            await i.response.send_message("❌ 出勤していません。", ephemeral=True); return False
        return True

    @discord.ui.button(label="🟢 出勤", style=discord.ButtonStyle.success, custom_id="v11_gen_in")
    async def cin(self, i, b):
        if not any(r.id == OMNIS_ROLE_ID for r in i.user.roles): return await i.response.send_message("❌ 権限なし", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT INTO work_logs VALUES (?,?,NULL)", (i.user.id, datetime.now())); await db.commit()
        await i.user.add_roles(i.guild.get_role(WORK_ROLE_ID))
        await i.response.send_message("🟢 出勤完了", ephemeral=True)

    @discord.ui.button(label="🔴 退勤", style=discord.ButtonStyle.danger, custom_id="v11_gen_out")
    async def cout(self, i, b):
        if not any(r.id == OMNIS_ROLE_ID for r in i.user.roles): return await i.response.send_message("❌ 権限なし", ephemeral=True)
        now = datetime.now()
        async with aiosqlite.connect(DB_PATH) as db:
            row = await (await db.execute("SELECT start FROM work_logs WHERE user_id=? AND end IS NULL ORDER BY start DESC LIMIT 1", (i.user.id,))).fetchone()
            if not row: return await i.response.send_message("❌ 記録なし", ephemeral=True)
            this_m = int((now - datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S.%f")).total_seconds() // 60)
            await db.execute("UPDATE work_logs SET end=? WHERE user_id=? AND end IS NULL", (now, i.user.id)); await db.commit()
        await i.user.remove_roles(i.guild.get_role(WORK_ROLE_ID))
        await i.response.send_message(f"🔴 退勤完了: {format_minutes(this_m)}", ephemeral=True)

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="v11_gen_craft")
    async def craft(self, i, b):
        if not await self.check_active_work(i): return
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name, current FROM products")).fetchall()
        if not prods: return await i.response.send_message("❌ 商品なし", ephemeral=True)
        v = discord.ui.View(); sel = discord.ui.Select(placeholder="商品を選択")
        for x in prods: sel.add_option(label=f"{x[0]} (在庫:{x[1]})", value=x[0])
        async def c_cb(i2, val):
            qty = int(val)
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE products SET current = current + ? WHERE name=?", (qty, sel.values[0])); await db.commit()
            await add_audit(i2.user.id, "制作", f"{sel.values[0]} x{qty}"); await i2.response.send_message(f"✅ 制作完了", ephemeral=True)
        sel.callback = lambda i2: i2.response.send_modal(GenericInputModal("制作", "個数", c_cb, default="1"))
        v.add_item(sel); await i.response.send_message("制作報告:", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上登録", style=discord.ButtonStyle.success, custom_id="v11_gen_sale")
    async def sale(self, i, b):
        if not await self.check_active_work(i): return
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name, price, current FROM products")).fetchall()
        if not prods: return await i.response.send_message("❌ 商品なし", ephemeral=True)
        v = discord.ui.View(); sel = discord.ui.Select(placeholder="商品を選択")
        for x in prods: sel.add_option(label=f"{x[0]} ({x[1]}円)", value=f"{x[0]}:{x[1]}")
        async def s_cb(i2, val):
            name, price = sel.values[0].split(":"); qty = int(val); total = qty * int(price)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE products SET current = current - ? WHERE name=?", (qty, name))
                await db.execute("INSERT INTO sales_ranking (user_id, total_amount) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET total_amount = total_amount + ?", (i2.user.id, total, total)); await db.commit()
            await add_audit(i2.user.id, "売上", f"{name} x{qty} ({total:,}円)"); await i2.response.send_message(f"💰 売上完了", ephemeral=True)
        sel.callback = lambda i2: i2.response.send_modal(GenericInputModal("売上", "数", s_cb, default="1"))
        v.add_item(sel); await i.response.send_message("売上登録:", view=v, ephemeral=True)

# ================= 4. 起動処理 =================
@bot.event
async def on_ready():
    await init_db()
    bot.add_view(AdminPanel()); bot.add_view(ItemPanel()); bot.add_view(GeneralPanel())
    print(f"Logged in as {bot.user}")
    for ch_id, view, text in [(ADMIN_PANEL_CH, AdminPanel(), "🔧 **管理者パネル**"), (ITEM_PANEL_CH, ItemPanel(), "📦 **商品管理パネル**"), (GENERAL_PANEL_CH, GeneralPanel(), "🧾 **業務パネル**")]:
        ch = bot.get_channel(ch_id)
        if ch: await ch.purge(limit=10); await ch.send(text, view=view)

bot.run(TOKEN)
