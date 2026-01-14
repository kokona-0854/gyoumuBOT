import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime
import os
from dotenv import load_dotenv

# ================= 1. 設定 =================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# 各種ID（ご自身のサーバーのIDに書き換えてください）
ROLE_OPTIONS = {
    "オムニス権限": 1459208662055911538,
    "管理者ロール": 1459388566760325318,
    "従業員ロール": 1455242976258297917,
}
ADMIN_ROLE_ID = 1459388566760325318  # 管理系パネルを操作できるロール
WORK_ROLE_ID = 1459209336076374068   # 勤務中ロール

# 送信先チャンネルID
ADMIN_PANEL_CH = 1459371812310745171    # 管理者パネル
ITEM_PANEL_CH = 1459371812310745171     # 商品管理パネル（同じでも可）
GENERAL_PANEL_CH = 1458801073899966585  # 業務パネル

DB_PATH = "master_system.db"
CURRENCY = "円"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= 2. DB初期化 & ログ関数 =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS work_logs(user_id INTEGER, start DATETIME, end DATETIME);
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

# ================= 3. モーダル定義 =================

class ItemAddModal(discord.ui.Modal):
    def __init__(self, mode):
        super().__init__(title="商品登録" if mode == "prod" else "素材登録")
        self.mode = mode
        self.name_in = discord.ui.TextInput(label="名前")
        self.add_item(self.name_in)
        if mode == "prod":
            self.price_in = discord.ui.TextInput(label="単価", default="1000")
            self.add_item(self.price_in)
    async def on_submit(self, i: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            if self.mode == "prod":
                await db.execute("INSERT OR REPLACE INTO products (name, price, current) VALUES (?,?, COALESCE((SELECT current FROM products WHERE name=?), 0))", (self.name_in.value, int(self.price_in.value), self.name_in.value))
            else:
                await db.execute("INSERT OR REPLACE INTO materials (name, current) VALUES (?, COALESCE((SELECT current FROM materials WHERE name=?), 0))", (self.name_in.value, self.name_in.value))
            await db.commit()
        await i.response.send_message(f"✅ {self.name_in.value} を登録/更新しました。", ephemeral=True)

class RecipeQtyModal(discord.ui.Modal, title="レシピ個数設定"):
    def __init__(self, p, m):
        super().__init__(); self.p, self.m = p, m
        self.q = discord.ui.TextInput(label=f"{m} の必要数", default="1")
        self.add_item(self.q)
    async def on_submit(self, i):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (self.p, self.m, int(self.q.value)))
            await db.commit()
        await i.response.send_message(f"✅ レシピ設定完了", ephemeral=True)

class StockAdjModal(discord.ui.Modal):
    def __init__(self, name):
        super().__init__(title=f"{name} の補充/引出")
        self.name = name
        self.q = discord.ui.TextInput(label="数量", placeholder="正の数で補充、負の数で引出")
        self.add_item(self.q)
    async def on_submit(self, i):
        val = int(self.q.value)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE materials SET current = current + ? WHERE name = ?", (val, self.name))
            await db.commit()
        await add_audit(i.user.id, "素材補充", f"{self.name} ({val:+})")
        await i.response.send_message(f"✅ 在庫を更新しました。", ephemeral=True)

class RoleModal(discord.ui.Modal, title="ユーザーID入力"):
    uid = discord.ui.TextInput(label="対象者ID")
    def __init__(self, rid, mode): super().__init__(); self.rid, self.mode = rid, mode
    async def on_submit(self, i):
        try:
            m = i.guild.get_member(int(self.uid.value))
            r = i.guild.get_role(self.rid)
            if self.mode == "add": await m.add_roles(r)
            else: await m.remove_roles(r)
            await i.response.send_message("✅ 更新完了", ephemeral=True)
        except: await i.response.send_message("❌ エラー", ephemeral=True)

class ResetIDModal(discord.ui.Modal, title="個別リセット"):
    uid = discord.ui.TextInput(label="リセットするユーザーID")
    def __init__(self, target): super().__init__(); self.target = target
    async def on_submit(self, i):
        async with aiosqlite.connect(DB_PATH) as db:
            if self.target == "rank": await db.execute("DELETE FROM sales_ranking WHERE user_id=?", (int(self.uid.value),))
            else: await db.execute("DELETE FROM work_logs WHERE user_id=?", (int(self.uid.value),))
            await db.commit()
        await i.response.send_message("✅ リセットしました。", ephemeral=True)

# ================= 4. View定義 =================

# --- 商品管理パネル ---
class ItemPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="📜 登録・レシピ・削除", style=discord.ButtonStyle.primary, custom_id="item_reg")
    async def reg(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            p = await (await db.execute("SELECT name FROM products")).fetchall()
            m = await (await db.execute("SELECT name FROM materials")).fetchall()
        v = discord.ui.View()
        v.add_item(discord.ui.Button(label="商品登録", style=discord.ButtonStyle.success)).callback = lambda x: x.response.send_modal(ItemAddModal("prod"))
        v.add_item(discord.ui.Button(label="素材登録", style=discord.ButtonStyle.success)).callback = lambda x: x.response.send_modal(ItemAddModal("mat"))
        if p and m:
            sel_r = discord.ui.Select(placeholder="レシピ設定(商品選択)")
            for x in p: sel_r.add_option(label=x[0], value=x[0])
            async def r_cb(i2):
                v2 = discord.ui.View(); sel_m = discord.ui.Select(placeholder="素材を選択")
                for x in m: sel_m.add_option(label=x[0], value=x[0])
                sel_m.callback = lambda i3: i3.response.send_modal(RecipeQtyModal(sel_r.values[0], sel_m.values[0]))
                v2.add_item(sel_m); await i2.response.send_message("素材選択:", view=v2, ephemeral=True)
            sel_r.callback = r_cb; v.add_item(sel_r)
        if p or m:
            sel_d = discord.ui.Select(placeholder="🗑️ 削除メニュー")
            for x in p: sel_d.add_option(label=f"商品消去: {x[0]}", value=f"p:{x[0]}")
            for x in m: sel_d.add_option(label=f"素材消去: {x[0]}", value=f"m:{x[0]}")
            async def d_cb(i2):
                t, n = sel_d.values[0].split(":")
                async with aiosqlite.connect(DB_PATH) as db:
                    if t == "p": await db.execute("DELETE FROM products WHERE name=?", (n,)); await db.execute("DELETE FROM recipes WHERE product_name=?", (n,))
                    else: await db.execute("DELETE FROM materials WHERE name=?", (n,)); await db.execute("DELETE FROM recipes WHERE material_name=?", (n,))
                    await db.commit()
                await i2.response.send_message(f"🗑️ {n} を削除しました。", ephemeral=True)
            sel_d.callback = d_cb; v.add_item(sel_d)
        await i.response.send_message("商品・レシピ・削除管理:", view=v, ephemeral=True)

    @discord.ui.button(label="📦 在庫・素材補充", style=discord.ButtonStyle.secondary, custom_id="item_stock")
    async def stock(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            m = await (await db.execute("SELECT name, current FROM materials")).fetchall()
            p = await (await db.execute("SELECT name, current FROM products")).fetchall()
        txt = "📦 **現在庫一覧**\n\n**素材:** " + (", ".join([f"{x[0]}(`{x[1]}`)" for x in m]) if m else "なし")
        txt += "\n**商品:** " + (", ".join([f"{x[0]}(`{x[1]}`)" for x in p]) if p else "なし")
        v = discord.ui.View()
        if m:
            s = discord.ui.Select(placeholder="補充する素材を選択")
            for x in m: s.add_option(label=x[0], value=x[0])
            s.callback = lambda i2: i2.response.send_modal(StockAdjModal(s.values[0]))
            v.add_item(s)
        await i.response.send_message(txt, view=v, ephemeral=True)

# --- 管理者パネル ---
class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="👤 メンバー管理", style=discord.ButtonStyle.success, custom_id="adm_mem")
    async def mem(self, i, b):
        v = discord.ui.View(); s = discord.ui.Select(placeholder="ロール選択")
        for n, rid in ROLE_OPTIONS.items(): s.add_option(label=n, value=str(rid))
        async def scb(i2):
            v2 = discord.ui.View(); rid = int(s.values[0])
            b1 = discord.ui.Button(label="付与", style=discord.ButtonStyle.primary); b1.callback = lambda x: x.response.send_modal(RoleModal(rid, "add"))
            b2 = discord.ui.Button(label="剥奪", style=discord.ButtonStyle.danger); b2.callback = lambda x: x.response.send_modal(RoleModal(rid, "rem"))
            v2.add_item(b1).add_item(b2); await i2.response.send_message("操作選択:", view=v2, ephemeral=True)
        s.callback = scb; v.add_item(s); await i.response.send_message("ロール管理:", view=v, ephemeral=True)

    @discord.ui.button(label="🏆 ランキング/勤務集計", style=discord.ButtonStyle.gray, custom_id="adm_stat")
    async def stat(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            rank = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
            work = await (await db.execute("SELECT user_id, SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE end IS NOT NULL GROUP BY user_id")).fetchall()
        txt = "🏆 **売上ランキング**\n" + "\n".join([f"{idx+1}. <@{r[0]}>: {r[1]:,}{CURRENCY}" for idx, r in enumerate(rank)]) if rank else "データなし"
        txt += "\n\n📊 **勤務集計**\n" + "\n".join([f"・<@{w[0]}>: `{int(w[1]//60)}分`" for w in work]) if work else "データなし"
        v = discord.ui.View()
        v.add_item(discord.ui.Button(label="売上個別リセット", style=discord.ButtonStyle.danger)).callback = lambda x: x.response.send_modal(ResetIDModal("rank"))
        v.add_item(discord.ui.Button(label="勤務個別リセット", style=discord.ButtonStyle.danger)).callback = lambda x: x.response.send_modal(ResetIDModal("work"))
        await i.response.send_message(txt, view=v, ephemeral=True)

    @discord.ui.button(label="📜 履歴ログ", style=discord.ButtonStyle.gray, custom_id="adm_log")
    async def logs(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, user_id, action, detail FROM audit_logs ORDER BY id DESC LIMIT 15")).fetchall()
        txt = "📜 **最新履歴 (15件)**\n" + "\n".join([f"`{r[0][5:16]}` <@{r[1]}> **{r[2]}**: {r[3]}" for r in rows]) if rows else "履歴なし"
        await i.response.send_message(txt, ephemeral=True)

# --- 業務パネル ---
class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🟢 出勤", style=discord.ButtonStyle.success, custom_id="gen_in")
    async def cin(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT INTO work_logs VALUES (?,?,NULL)", (i.user.id, datetime.now())); await db.commit()
        await i.user.add_roles(i.guild.get_role(WORK_ROLE_ID))
        await i.response.send_message("🟢 出勤完了", ephemeral=True)

    @discord.ui.button(label="🔴 退勤", style=discord.ButtonStyle.danger, custom_id="gen_out")
    async def cout(self, i, b):
        now = datetime.now()
        async with aiosqlite.connect(DB_PATH) as db:
            row = await (await db.execute("SELECT start FROM work_logs WHERE user_id=? AND end IS NULL ORDER BY start DESC LIMIT 1", (i.user.id,))).fetchone()
            if not row: return await i.response.send_message("❌ 出勤記録なし", ephemeral=True)
            this_m = int((now - datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S.%f")).total_seconds() // 60)
            await db.execute("UPDATE work_logs SET end=? WHERE user_id=? AND end IS NULL", (now, i.user.id))
            total = await (await db.execute("SELECT SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE user_id=? AND end IS NOT NULL", (i.user.id,))).fetchone()
            await db.commit()
        await i.user.remove_roles(i.guild.get_role(WORK_ROLE_ID))
        await i.response.send_message(f"🔴 退勤完了\n今回: `{this_m}分` / 累計: `{int(total[0]//60)}分`", ephemeral=True)

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="gen_craft")
    async def craft(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name, current FROM products")).fetchall()
        if not prods: return await i.response.send_message("❌ 商品未登録", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="商品を選択")
        for x in prods: s.add_option(label=f"{x[0]} (在庫:{x[1]})", value=x[0])
        async def scb(i2):
            class CraftModal(discord.ui.Modal, title=f"{s.values[0]} 制作"):
                q = discord.ui.TextInput(label="制作数", default="1")
                async def on_submit(self, i3):
                    qty = int(self.q.value)
                    async with aiosqlite.connect(DB_PATH) as db:
                        recs = await (await db.execute("SELECT material_name, quantity FROM recipes WHERE product_name=?", (s.values[0],))).fetchall()
                        for mn, mq in recs:
                            stk = await (await db.execute("SELECT current FROM materials WHERE name=?", (mn,))).fetchone()
                            if not stk or stk[0] < (mq*qty): return await i3.response.send_message(f"❌ 素材不足: {mn}", ephemeral=True)
                        for mn, mq in recs: await db.execute("UPDATE materials SET current = current - ? WHERE name=?", (mq*qty, mn))
                        await db.execute("UPDATE products SET current = current + ? WHERE name=?", (qty, s.values[0]))
                        new = await (await db.execute("SELECT current FROM products WHERE name=?", (s.values[0],))).fetchone()
                        await db.commit()
                    await add_audit(i3.user.id, "制作", f"{s.values[0]} x{qty} (新在庫:{new[0]})")
                    await i3.response.send_message(f"✅ 制作完了！現在庫: `{new[0]}`", ephemeral=True)
            await i2.response.send_modal(CraftModal())
        s.callback = scb; v.add_item(s); await i.response.send_message("制作報告:", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上登録", style=discord.ButtonStyle.success, custom_id="gen_sale")
    async def sale(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name, price, current FROM products")).fetchall()
        if not prods: return await i.response.send_message("❌ 商品未登録", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="販売した商品を選択")
        for x in prods: s.add_option(label=f"{x[0]} ({x[1]}{CURRENCY} / 在庫:{x[2]})", value=f"{x[0]}:{x[1]}")
        async def scb(i2):
            name, price = s.values[0].split(":")
            class SaleModal(discord.ui.Modal, title=f"{name} 販売"):
                q = discord.ui.TextInput(label="販売数", default="1")
                async def on_submit(self, i3):
                    qty = int(self.q.value); amt = qty * int(price)
                    async with aiosqlite.connect(DB_PATH) as db:
                        stk = await (await db.execute("SELECT current FROM products WHERE name=?", (name,))).fetchone()
                        if not stk or stk[0] < qty: return await i3.response.send_message("❌ 在庫不足", ephemeral=True)
                        await db.execute("UPDATE products SET current = current - ? WHERE name=?", (qty, name))
                        await db.execute("INSERT INTO sales_ranking (user_id, total_amount) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET total_amount = total_amount + ?", (i3.user.id, amt, amt))
                        new = await (await db.execute("SELECT current FROM products WHERE name=?", (name,))).fetchone()
                        await db.commit()
                    await add_audit(i3.user.id, "売上", f"{name} x{qty} (残在庫:{new[0]})")
                    await i3.response.send_message(f"💰 売上登録完了！残在庫: `{new[0]}` / 金額: `{amt:,}{CURRENCY}`", ephemeral=True)
            await i2.response.send_modal(SaleModal())
        s.callback = scb; v.add_item(s); await i.response.send_message("売上登録:", view=v, ephemeral=True)

# ================= 5. 起動処理 =================
@bot.event
async def on_ready():
    await init_db()
    bot.add_view(AdminPanel()); bot.add_view(ItemPanel()); bot.add_view(GeneralPanel())
    print(f"Logged in as {bot.user}")

    setup = [
        (ADMIN_PANEL_CH, AdminPanel(), "🔧 **管理者パネル**"),
        (ITEM_PANEL_CH, ItemPanel(), "📦 **商品管理パネル**"),
        (GENERAL_PANEL_CH, GeneralPanel(), "🧾 **業務パネル**")
    ]
    for ch_id, view, title in setup:
        ch = bot.get_channel(ch_id)
        if ch:
            await ch.purge(limit=5)
            await ch.send(title, view=view)

bot.run(TOKEN)
