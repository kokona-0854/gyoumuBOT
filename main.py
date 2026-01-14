import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime
import os
from dotenv import load_dotenv

# ================= 1. 設定 =================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# 各種ID設定
ROLE_OPTIONS = {
    "オムニス権限": 1459208662055911538,
    "管理者ロール": 1459388566760325318,
    "会頭ロール": 1454307785717321738,
    "交易師ロール": 1454310938017661031,
    "従業員ロール": 1455242976258297917,
    "アルバイトロール": 1455243576337502228
}

WORK_ROLE_ID = 1459209336076374068 
ADMIN_PANEL_CHANNEL_ID = 1459371812310745171
GENERAL_PANEL_CHANNEL_ID = 1458801073899966585

DB_PATH = "data.db"
CURRENCY = "円"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= 2. DB初期化 & ログ =================
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
        await db.execute("INSERT INTO audit_logs (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
                        (user_id, action, detail, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        await db.commit()

def format_time(seconds):
    if seconds is None: return "0分"
    h, m = int(seconds // 3600), int((seconds % 3600) // 60)
    return f"{h}時間{m}分"

# ================= 3. モーダル定義 =================

class RoleInputModal(discord.ui.Modal, title="ユーザーID入力"):
    uid = discord.ui.TextInput(label="対象者のユーザーID", placeholder="123...")
    def __init__(self, rid, mode): super().__init__(); self.rid, self.mode = rid, mode
    async def on_submit(self, i: discord.Interaction):
        try:
            m = i.guild.get_member(int(self.uid.value))
            r = i.guild.get_role(self.rid)
            if self.mode == "add": await m.add_roles(r)
            else: await m.remove_roles(r)
            await add_audit(i.user.id, "ROLE", f"{m.display_name} {self.mode} {r.name}")
            await i.response.send_message(f"✅ ロール操作完了", ephemeral=True)
        except: await i.response.send_message("❌ IDが正しくないかユーザーがいません", ephemeral=True)

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
                await db.execute("INSERT OR REPLACE INTO products (name, price) VALUES (?,?)", (self.name_in.value, int(self.price_in.value)))
            else:
                await db.execute("INSERT OR REPLACE INTO materials (name) VALUES (?)", (self.name_in.value,))
            await db.commit()
        await i.response.send_message(f"✅ {self.name_in.value} を登録", ephemeral=True)

# ================= 4. 管理パネル =================

class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="👤 メンバー管理", style=discord.ButtonStyle.success, custom_id="adm_mem_v9")
    async def member_mgmt(self, interaction, button):
        v = discord.ui.View(); s = discord.ui.Select(placeholder="ロールを選択")
        for n, rid in ROLE_OPTIONS.items(): s.add_option(label=n, value=str(rid))
        async def scb(i):
            rid = int(s.values[0]); v2 = discord.ui.View()
            b1 = discord.ui.Button(label="付与", style=discord.ButtonStyle.success); b1.callback = lambda i2: i2.response.send_modal(RoleInputModal(rid, "add"))
            b2 = discord.ui.Button(label="削除", style=discord.ButtonStyle.danger); b2.callback = lambda i2: i2.response.send_modal(RoleInputModal(rid, "rem"))
            v2.add_item(b1).add_item(b2); await i.response.send_message("操作:", view=v2, ephemeral=True)
        s.callback = scb; v.add_item(s); await interaction.response.send_message("ロール管理:", view=v, ephemeral=True)

    @discord.ui.button(label="📦 在庫一覧", style=discord.ButtonStyle.secondary, custom_id="adm_stk_v9")
    async def stock_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            m = await (await db.execute("SELECT name, current FROM materials")).fetchall()
            p = await (await db.execute("SELECT name, current FROM products")).fetchall()
        txt = "📦 **在庫状況**\n" + "\n".join([f"・{x[0]}: `{x[1]}`" for x in m+p])
        await interaction.response.send_message(txt if (m or p) else "在庫なし", ephemeral=True)

    @discord.ui.button(label="📜 登録管理", style=discord.ButtonStyle.primary, custom_id="adm_reg_v9")
    async def reg_mgmt(self, interaction, button):
        v = discord.ui.View()
        b1 = discord.ui.Button(label="商品追加", style=discord.ButtonStyle.success); b1.callback = lambda i: i.response.send_modal(ItemAddModal("prod"))
        b2 = discord.ui.Button(label="素材追加", style=discord.ButtonStyle.success); b2.callback = lambda i: i.response.send_modal(ItemAddModal("mat"))
        v.add_item(b1).add_item(b2); await interaction.response.send_message("登録:", view=v, ephemeral=True)

    @discord.ui.button(label="🏆 ランキング", style=discord.ButtonStyle.success, custom_id="adm_rank_v9")
    async def view_rank(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
        txt = "🏆 **売上ランキング**\n" + "\n".join([f"{i+1}位: <@{r[0]}> `{r[1]:,}`{CURRENCY}" for i, r in enumerate(rows)])
        await interaction.response.send_message(txt if rows else "データなし", ephemeral=True)

    @discord.ui.button(label="⏰ 勤務集計", style=discord.ButtonStyle.gray, custom_id="adm_work_v9")
    async def work_sum(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT user_id, SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE end IS NOT NULL GROUP BY user_id")).fetchall()
        txt = "📊 **合計勤務時間**\n" + "\n".join([f"・<@{r[0]}>: `{format_time(r[1])}`" for r in rows])
        await interaction.response.send_message(txt if rows else "履歴なし", ephemeral=True)

    @discord.ui.button(label="📜 履歴ログ", style=discord.ButtonStyle.gray, custom_id="adm_log_v9")
    async def view_audit(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, user_id, action, detail FROM audit_logs ORDER BY id DESC LIMIT 10")).fetchall()
        log_lines = [f"`{r[0][5:16]}` <@{r[1]}> **{r[2]}**: {r[3]}" for r in rows]
        await interaction.response.send_message("📜 **最新履歴**\n" + "\n".join(log_lines) if rows else "履歴なし", ephemeral=True)

# ================= 5. 業務パネル =================

class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🟢 出勤", style=discord.ButtonStyle.success, custom_id="gen_in_v9")
    async def in_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO work_logs VALUES (?,?,NULL)", (interaction.user.id, datetime.now())); await db.commit()
        await interaction.user.add_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message("🟢 出勤", ephemeral=True)

    @discord.ui.button(label="🔴 退勤", style=discord.ButtonStyle.danger, custom_id="gen_out_v9")
    async def out_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE work_logs SET end=? WHERE user_id=? AND end IS NULL", (datetime.now(), interaction.user.id)); await db.commit()
        await interaction.user.remove_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message("🔴 退勤", ephemeral=True)

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="gen_craft_v9")
    async def craft_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name FROM products")).fetchall()
        if not prods: return await interaction.response.send_message("❌ 商品登録がありません", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="作った商品を選択")
        for p in prods: s.add_option(label=p[0], value=p[0])
        async def scb(i):
            class CModal(discord.ui.Modal, title="制作個数"):
                q = discord.ui.TextInput(label="個数", default="1")
                async def on_submit(self, i2):
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE products SET current = current + ? WHERE name=?", (int(self.q.value), s.values[0]))
                        await db.commit()
                    await add_audit(i2.user.id, "CRAFT", f"{s.values[0]} x{self.q.value}")
                    await i2.response.send_message(f"✅ {s.values[0]}を{self.q.value}個 制作登録しました", ephemeral=True)
            await i.response.send_modal(CModal())
        s.callback = scb; v.add_item(s); await interaction.response.send_message("制作報告:", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上登録", style=discord.ButtonStyle.success, custom_id="gen_sale_v9")
    async def sale_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name, price FROM products")).fetchall()
        if not prods: return await interaction.response.send_message("❌ 商品登録がありません", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="売れた商品を選択")
        for p in prods: s.add_option(label=f"{p[0]} ({p[1]:,}{CURRENCY})", value=f"{p[0]}:{p[1]}")
        async def scb(i):
            pn, pp = s.values[0].split(":")
            class SModal(discord.ui.Modal, title="販売個数"):
                q = discord.ui.TextInput(label="個数", default="1")
                async def on_submit(self, i2):
                    amt = int(self.q.value) * int(pp)
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("INSERT INTO sales_ranking (user_id, total_amount) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET total_amount = total_amount + ?", (i2.user.id, amt, amt))
                        await db.execute("UPDATE products SET current = current - ? WHERE name=?", (int(self.q.value), pn))
                        await db.commit()
                    await add_audit(i2.user.id, "SALE", f"{pn} x{self.q.value} ({amt:,}{CURRENCY})")
                    await i2.response.send_message(f"💰 {amt:,}{CURRENCY} 登録完了", ephemeral=True)
            await i.response.send_modal(SModal())
        s.callback = scb; v.add_item(s); await interaction.response.send_message("売上入力:", view=v, ephemeral=True)

# ================= 6. 起動 =================
@bot.event
async def on_ready():
    await init_db()
    bot.add_view(AdminPanel()); bot.add_view(GeneralPanel())
    print(f"Logged in as {bot.user}")
    for c_id, view, txt in [(ADMIN_PANEL_CHANNEL_ID, AdminPanel(), "🔧 **管理パネル**"), (GENERAL_PANEL_CHANNEL_ID, GeneralPanel(), "🧾 **業務パネル**")]:
        ch = bot.get_channel(c_id)
        if ch: await ch.purge(limit=5); await ch.send(txt, view=view)

bot.run(TOKEN)
