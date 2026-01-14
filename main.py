import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime
import os
from dotenv import load_dotenv

# ================= 1. 各種設定 =================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ロールID設定
ADMIN_ROLE_ID = 1459388566760325318      # 管理者（管理・商品パネル操作可能）
OMNIS_ROLE_ID = 1459208662055911538      # オムニス商会（出退勤可能）
WORK_ROLE_ID = 1459209336076374068       # 出勤中（制作・売上報告に必須）

# チャンネルID設定
ADMIN_PANEL_CH = 1459371812310745171     # 管理パネル設置
ITEM_PANEL_CH = 1461057553021538485      # 商品パネル設置
GENERAL_PANEL_CH = 1458801073899966585   # 業務パネル設置

# メンバー管理用ロール選択肢
ROLE_OPTIONS = {
    "会頭": 1454307785717321738,
    "交易師": 1454310938017661031,
    "従業員": 1455242976258297917,
    "アルバイト": 1455243576337502228
}

DB_PATH = "master_system_v14.db"
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= 2. データベース & 共通関数 =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS work_logs(user_id INTEGER, start DATETIME, end DATETIME, duration INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS materials(name TEXT PRIMARY KEY, current INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS products(name TEXT PRIMARY KEY, price INTEGER DEFAULT 0, current INTEGER DEFAULT 0);
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
    hrs = int(total_minutes // 60)
    mins = int(total_minutes % 60)
    return f"{hrs}時間{mins}分"

class GenericInputModal(discord.ui.Modal):
    def __init__(self, title, label, callback_func, placeholder=None, default=None):
        super().__init__(title=title)
        self.input_field = discord.ui.TextInput(label=label, placeholder=placeholder, default=default)
        self.add_item(self.input_field); self.callback_func = callback_func
    async def on_submit(self, interaction: discord.Interaction): await self.callback_func(interaction, self.input_field.value)

# ================= 3. 商品パネル (ItemPanel) =================
class ItemPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    async def interaction_check(self, i: discord.Interaction):
        if any(r.id == ADMIN_ROLE_ID for r in i.user.roles): return True
        await i.response.send_message("❌ 管理者ロールが必要です。", ephemeral=True); return False

    @discord.ui.button(label="📜 商品・素材｜レシピ設定", style=discord.ButtonStyle.primary, custom_id="v14_it_reg")
    async def reg_menu(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            p = await (await db.execute("SELECT name FROM products")).fetchall()
            m = await (await db.execute("SELECT name FROM materials")).fetchall()
        
        view = discord.ui.View()
        async def add_p(idx, v):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR IGNORE INTO products (name) VALUES (?)", (v,)); await db.commit()
            await idx.response.send_message(f"✅ 商品 {v} 登録", ephemeral=True)
        async def add_m(idx, v):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR IGNORE INTO materials (name) VALUES (?)", (v,)); await db.commit()
            await idx.response.send_message(f"✅ 素材 {v} 登録", ephemeral=True)
        
        view.add_item(discord.ui.Button(label="商品追加", style=discord.ButtonStyle.success)).callback = lambda x: x.response.send_modal(GenericInputModal("登録", "商品名", add_p))
        view.add_item(discord.ui.Button(label="素材追加", style=discord.ButtonStyle.success)).callback = lambda x: x.response.send_modal(GenericInputModal("登録", "素材名", add_m))
        
        if p:
            sel_p = discord.ui.Select(placeholder="レシピ/価格設定する商品を選択", row=1)
            for x in p: sel_p.add_option(label=f"商品: {x[0]}", value=x[0])
            async def p_sel_cb(i2):
                v3 = discord.ui.View()
                async def prc_set(i3, v):
                    async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE products SET price=? WHERE name=?", (int(v), sel_p.values[0])); await db.commit()
                    await i3.response.send_message(f"✅ {sel_p.values[0]} の単価を {v}円 に設定しました。", ephemeral=True)
                
                v3.add_item(discord.ui.Button(label="💰 単価設定", style=discord.ButtonStyle.primary)).callback = lambda x: x.response.send_modal(GenericInputModal("価格", "単価", prc_set))
                
                if m:
                    sel_m = discord.ui.Select(placeholder="レシピに素材を追加")
                    for mx in m: sel_m.add_option(label=f"素材: {mx[0]}", value=mx[0])
                    async def m_fin(i4, qty):
                        async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (sel_p.values[0], sel_m.values[0], int(qty))); await db.commit()
                        await i4.response.send_message(f"✅ {sel_p.values[0]} に {sel_m.values[0]} x{qty} を設定", ephemeral=True)
                    sel_m.callback = lambda i5: i5.response.send_modal(GenericInputModal("個数", "必要個数", m_fin))
                    v3.add_item(sel_m)
                
                await i2.response.send_message(f"【{sel_p.values[0]}】の設定:", view=v3, ephemeral=True)
            sel_p.callback = p_sel_cb; view.add_item(sel_p)

        await i.response.send_message("登録・レシピメニュー:", view=view, ephemeral=True)

    @discord.ui.button(label="📦 在庫確認・素材調整", style=discord.ButtonStyle.secondary, custom_id="v14_it_stock")
    async def stock_menu(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            m = await (await db.execute("SELECT name, current FROM materials")).fetchall()
            p = await (await db.execute("SELECT name, current FROM products")).fetchall()
        
        txt = "📦 **現在在庫一覧**\n"
        txt += "**【素材】**\n" + ("\n".join([f"・{x[0]}: {x[1]}個" for x in m]) if m else "なし")
        txt += "\n\n**【商品】**\n" + ("\n".join([f"・{x[0]}: {x[1]}個" for x in p]) if p else "なし")
        
        view = discord.ui.View()
        if m:
            sel = discord.ui.Select(placeholder="素材の補充/引出")
            for x in m: sel.add_option(label=x[0], value=x[0])
            async def adj(i2, v):
                async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE materials SET current = current + ? WHERE name=?", (int(v), sel.values[0])); await db.commit()
                await i2.response.send_message(f"✅ {sel.values[0]} を {v} 調整しました。", ephemeral=True)
            sel.callback = lambda i2: i2.response.send_modal(GenericInputModal("調整", "数 (+補充 / -引出)", adj))
            view.add_item(sel)
        await i.response.send_message(txt, view=view, ephemeral=True)

# ================= 4. 管理パネル (AdminPanel) =================
class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    async def interaction_check(self, i: discord.Interaction):
        if any(r.id == ADMIN_ROLE_ID for r in i.user.roles): return True
        await i.response.send_message("❌ 管理者ロールが必要です。", ephemeral=True); return False

    @discord.ui.button(label="👤 メンバー管理", style=discord.ButtonStyle.success, custom_id="v14_ad_mem")
    async def member(self, i, b):
        view = discord.ui.View(); sel = discord.ui.Select(placeholder="付与するロールを選択")
        for n, rid in ROLE_OPTIONS.items(): sel.add_option(label=n, value=str(rid))
        async def m_cb(i2):
            rid = int(sel.values[0])
            async def act(i3, uid):
                target = i3.guild.get_member(int(uid)); role = i3.guild.get_role(rid)
                await target.add_roles(role); await i3.response.send_message(f"✅ {target.display_name} に付与完了", ephemeral=True)
            await i2.response.send_modal(GenericInputModal("ID入力", "ユーザーID", act))
        sel.callback = m_cb; view.add_item(sel); await i.response.send_message("管理:", view=view, ephemeral=True)

    @discord.ui.button(label="🏆 ランキング/勤怠集計", style=discord.ButtonStyle.gray, custom_id="v14_ad_stat")
    async def stats(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            rank = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
            work = await (await db.execute("SELECT user_id, SUM(duration) FROM work_logs GROUP BY user_id")).fetchall()
        
        msg = "🏆 **売上ランキング**\n" + "\n".join([f"<@{r[0]}>: {r[1]:,}円" for r in rank])
        msg += "\n\n📊 **勤怠集計**\n" + "\n".join([f"<@{w[0]}>: {format_minutes(w[1])}" for w in work])
        
        view = discord.ui.View()
        async def res_all(idx):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM sales_ranking; DELETE FROM work_logs;"); await db.commit()
            await idx.response.send_message("✅ 全データをリセットしました。", ephemeral=True)
        view.add_item(discord.ui.Button(label="全体リセット", style=discord.ButtonStyle.danger)).callback = res_all
        await i.response.send_message(msg, view=view, ephemeral=True)

    @discord.ui.button(label="📜 履歴ログ", style=discord.ButtonStyle.gray, custom_id="v14_ad_log")
    async def logs(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, user_id, action, detail FROM audit_logs ORDER BY id DESC LIMIT 15")).fetchall()
        txt = "📜 **履歴**\n" + "\n".join([f"`{r[0][5:16]}` <@{r[1]}> **{r[2]}**: {r[3]}" for r in rows])
        await i.response.send_message(txt, ephemeral=True)

# ================= 5. 業務パネル (GeneralPanel) =================
class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    @discord.ui.button(label="🟢 出勤 / 🔴 退勤", style=discord.ButtonStyle.success, custom_id="v14_gen_work")
    async def work_toggle(self, i, b):
        if not any(r.id == OMNIS_ROLE_ID for r in i.user.roles):
            return await i.response.send_message("❌ オムニス商会ロールが必要です。", ephemeral=True)
        now = datetime.now()
        async with aiosqlite.connect(DB_PATH) as db:
            active = await (await db.execute("SELECT start FROM work_logs WHERE user_id=? AND end IS NULL", (i.user.id,))).fetchone()
            if not active:
                await db.execute("INSERT INTO work_logs (user_id, start) VALUES (?,?)", (i.user.id, now))
                await i.user.add_roles(i.guild.get_role(WORK_ROLE_ID))
                await i.response.send_message("🟢 出勤完了", ephemeral=True)
            else:
                diff = int((now - datetime.strptime(active[0], "%Y-%m-%d %H:%M:%S.%f")).total_seconds() // 60)
                await db.execute("UPDATE work_logs SET end=?, duration=? WHERE user_id=? AND end IS NULL", (now, diff, i.user.id))
                await i.user.remove_roles(i.guild.get_role(WORK_ROLE_ID))
                await i.response.send_message(f"🔴 退勤完了: 勤務時間 `{format_minutes(diff)}`", ephemeral=True)
            await db.commit()

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="v14_gen_craft")
    async def craft(self, i, b):
        if not any(r.id == WORK_ROLE_ID for r in i.user.roles): return await i.response.send_message("❌ 出勤してください。", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name FROM products")).fetchall()
        
        v = discord.ui.View(); sel = discord.ui.Select(placeholder="制作した商品を選択")
        for p in prods: sel.add_option(label=p[0], value=p[0])
        
        async def fin(i2, qty):
            q = int(qty)
            async with aiosqlite.connect(DB_PATH) as db:
                recipe = await (await db.execute("SELECT material_name, quantity FROM recipes WHERE product_name=?", (sel.values[0],))).fetchall()
                if not recipe: return await i2.response.send_message("❌ レシピ未設定", ephemeral=True)
                for mn, mq in recipe:
                    stk = await (await db.execute("SELECT current FROM materials WHERE name=?", (mn,))).fetchone()
                    if not stk or stk[0] < (mq * q): return await i2.response.send_message(f"❌ 素材不足: {mn}", ephemeral=True)
                for mn, mq in recipe: await db.execute("UPDATE materials SET current = current - ? WHERE name=?", (mq * q, mn))
                await db.execute("UPDATE products SET current = current + ? WHERE name=?", (q, sel.values[0]))
                await db.commit()
            await add_audit(i2.user.id, "制作", f"{sel.values[0]} x{q}"); await i2.response.send_message("✅ 報告完了", ephemeral=True)
        sel.callback = lambda i3: i3.response.send_modal(GenericInputModal("制作", "個数", fin)); v.add_item(sel)
        await i.response.send_message("選択:", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上報告", style=discord.ButtonStyle.success, custom_id="v14_gen_sale")
    async def sale(self, i, b):
        if not any(r.id == WORK_ROLE_ID for r in i.user.roles): return await i.response.send_message("❌ 出勤してください。", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name, price FROM products")).fetchall()
        
        v = discord.ui.View(); sel = discord.ui.Select(placeholder="売れた商品")
        for p in prods: sel.add_option(label=f"{p[0]} ({p[1]}円)", value=f"{p[0]}:{p[1]}")
        
        async def fin(i2, qty):
            name, price = sel.values[0].split(":"); q = int(qty); total = q * int(price)
            async with aiosqlite.connect(DB_PATH) as db:
                stk = await (await db.execute("SELECT current FROM products WHERE name=?", (name,))).fetchone()
                if not stk or stk[0] < q: return await i2.response.send_message("❌ 在庫不足", ephemeral=True)
                await db.execute("UPDATE products SET current = current - ? WHERE name=?", (q, name))
                await db.execute("INSERT INTO sales_ranking (user_id, total_amount) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET total_amount = total_amount + ?", (i2.user.id, total, total))
                await db.commit()
            await add_audit(i2.user.id, "売上", f"{name} x{q} ({total:,}円)"); await i2.response.send_message("💰 売上完了", ephemeral=True)
        sel.callback = lambda i3: i3.response.send_modal(GenericInputModal("売上", "個数", fin)); v.add_item(sel)
        await i.response.send_message("選択:", view=v, ephemeral=True)

# ================= 6. 起動処理 =================
@bot.event
async def on_ready():
    await init_db()
    bot.add_view(AdminPanel()); bot.add_view(ItemPanel()); bot.add_view(GeneralPanel())
    print(f"Logged in as {bot.user}")
    for cid, view, txt in [(ADMIN_PANEL_CH, AdminPanel(), "🔧 **管理パネル**"), (ITEM_PANEL_CH, ItemPanel(), "📦 **商品パネル**"), (GENERAL_PANEL_CH, GeneralPanel(), "🧾 **業務パネル**")]:
        ch = bot.get_channel(cid)
        if ch: await ch.purge(limit=5); await ch.send(txt, view=view)

bot.run(TOKEN)
