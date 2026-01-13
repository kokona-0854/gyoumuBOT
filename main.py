import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime
import os
from dotenv import load_dotenv

# ================= 1. 設定 =================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# 管理パネルで選択できるロールのリスト（名前: ID）
ROLE_OPTIONS = {
    "オムニス権限": 1459208662055911538,
    "管理者ロール": 1459388566760325318,
    "会頭ロール": 1454307785717321738,
    "交易師ロール": 1454310938017661031,
    "従業員ロール": 1455242976258297917,
    "アルバイトロール": 1455243576337502228
}

# 基本設定
OMNIS_ROLE_ID = 1459208662055911538
WORK_ROLE_ID = 1459209336076374068
ADMIN_ROLE_ID = 1459388566760325318

ADMIN_PANEL_CHANNEL_ID = 1459371812310745171
GENERAL_PANEL_CHANNEL_ID = 1458801073899966585
ALERT_CHANNEL_ID = 1460745784491380799 # 在庫不足の通知先

DB_PATH = "data.db"
CURRENCY = "円"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= 2. データベース & 共通関数 =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(f"""
        CREATE TABLE IF NOT EXISTS work_logs(user_id INTEGER, start DATETIME, end DATETIME);
        CREATE TABLE IF NOT EXISTS materials(name TEXT PRIMARY KEY, current INTEGER, threshold INTEGER);
        CREATE TABLE IF NOT EXISTS products(name TEXT PRIMARY KEY, price INTEGER, current INTEGER DEFAULT 0, threshold INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS recipes(product_name TEXT, material_name TEXT, quantity INTEGER, PRIMARY KEY(product_name, material_name));
        CREATE TABLE IF NOT EXISTS sales_ranking(user_id INTEGER PRIMARY KEY, total_amount INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, detail TEXT, created_at DATETIME);
        """)
        await db.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('hourly_bonus', '0')")
        await db.commit()

async def add_audit(user_id, action, detail):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO audit_logs (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
                        (user_id, action, detail, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        await db.commit()

async def get_config(key, default="0"):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default

async def check_alerts(item_name, item_type="material"):
    async with aiosqlite.connect(DB_PATH) as db:
        table = "materials" if item_type == "material" else "products"
        row = await (await db.execute(f"SELECT current, threshold FROM {table} WHERE name=?", (item_name,))).fetchone()
        if row and row[0] < row[1]:
            channel = bot.get_channel(ALERT_CHANNEL_ID)
            if channel:
                await channel.send(f"⚠️ **【在庫不足アラート】**\n{item_type == 'material' and '素材' or '商品'}「**{item_name}**」の在庫が目標を下回りました。\n現在: **{row[0]}** / 目標: **{row[1]}**")

def format_time(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{int(hours)}時間{int(minutes)}分"

# ================= 3. モーダルクラス =================

class RoleActionModal(discord.ui.Modal):
    def __init__(self, role_id, mode_label):
        super().__init__(title=f"ロール{mode_label}")
        self.role_id = role_id
        self.mode_label = mode_label
        self.uid_input = discord.ui.TextInput(label="対象のユーザーID", placeholder="数字のみ入力")
        self.add_item(self.uid_input)
    async def on_submit(self, interaction):
        try:
            uid = int(self.uid_input.value)
            member = interaction.guild.get_member(uid)
            role = interaction.guild.get_role(self.role_id)
            if not member or not role: return await interaction.response.send_message("❌ 無効なIDまたはロールです。", ephemeral=True)
            if "付与" in self.mode_label: await member.add_roles(role)
            else: await member.remove_roles(role)
            await add_audit(interaction.user.id, "MEMBER_MGMT", f"{member.display_name} へ {role.name} を{self.mode_label}")
            await interaction.response.send_message(f"✅ {member.display_name} に対して {role.name} を{self.mode_label}しました。", ephemeral=True)
        except: await interaction.response.send_message("❌ エラーが発生しました。", ephemeral=True)

class BonusSetModal(discord.ui.Modal, title="ボーナス設定"):
    amt = discord.ui.TextInput(label="1時間あたりの支給額", placeholder="例: 5000")
    async def on_submit(self, interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('hourly_bonus', ?)", (self.amt.value,))
            await db.commit()
        await add_audit(interaction.user.id, "SET_BONUS", f"{self.amt.value}{CURRENCY}")
        await interaction.response.send_message(f"✅ 時給を {self.amt.value}{CURRENCY} に設定しました。", ephemeral=True)

class ProductDefineModal(discord.ui.Modal, title="商品登録"):
    name = discord.ui.TextInput(label="商品名")
    price = discord.ui.TextInput(label="販売価格")
    threshold = discord.ui.TextInput(label="必要在庫数(アラート閾値)", default="5")
    async def on_submit(self, interaction):
        try:
            p, t = int(self.price.value), int(self.threshold.value)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR REPLACE INTO products (name, price, current, threshold) VALUES (?,?,0,?)", (self.name.value, p, t))
                await db.commit()
            await add_audit(interaction.user.id, "PROD_REG", f"{self.name.value}")
            await interaction.response.send_message(f"✅ 商品「{self.name.value}」を登録しました。", ephemeral=True)
        except: await interaction.response.send_message("❌ 数値エラー", ephemeral=True)

class MaterialAddModal(discord.ui.Modal, title="素材登録"):
    name = discord.ui.TextInput(label="素材名")
    threshold = discord.ui.TextInput(label="目標在庫(アラート閾値)", default="10")
    async def on_submit(self, interaction):
        try:
            t = int(self.threshold.value)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR REPLACE INTO materials VALUES (?, 0, ?)", (self.name.value, t))
                await db.commit()
            await add_audit(interaction.user.id, "MAT_REG", self.name.value)
            await interaction.response.send_message(f"✅ 素材「{self.name.value}」を登録しました。", ephemeral=True)
        except: await interaction.response.send_message("❌ エラー", ephemeral=True)

class RecipeSetModal(discord.ui.Modal):
    def __init__(self, p_name, m_name):
        super().__init__(title=f"レシピ: {p_name}")
        self.p_name, self.m_name = p_name, m_name
        self.qty = discord.ui.TextInput(label=f"{m_name} の必要数", default="1")
    async def on_submit(self, interaction):
        try:
            v = int(self.qty.value)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (self.p_name, self.m_name, v))
                await db.commit()
            await interaction.response.send_message(f"✅ {self.p_name} 制作に {self.m_name}x{v} を登録しました。", ephemeral=True)
        except: await interaction.response.send_message("❌ 数値エラー", ephemeral=True)

# ================= 4. パネル View クラス =================

class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🟢 出勤", style=discord.ButtonStyle.success, custom_id="g_in")
    async def in_btn(self, interaction, button):
        if OMNIS_ROLE_ID not in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message("⛔ 権限がありません。", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO work_logs VALUES (?,?,NULL)", (interaction.user.id, datetime.now()))
            await db.commit()
        await interaction.user.add_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message("🟢 出勤しました。", ephemeral=True)

    @discord.ui.button(label="🔴 退勤", style=discord.ButtonStyle.danger, custom_id="g_out")
    async def out_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            row = await (await db.execute("SELECT rowid, start FROM work_logs WHERE user_id=? AND end IS NULL", (interaction.user.id,))).fetchone()
            if not row: return await interaction.response.send_message("❌ 出勤データなし。", ephemeral=True)
            end_t = datetime.now()
            await db.execute("UPDATE work_logs SET end=? WHERE rowid=?", (end_t, row[0]))
            await db.commit()
            start_t = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S.%f") if "." in row[1] else datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")
            diff = (end_t - start_t).total_seconds()
        await interaction.user.remove_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message(f"🔴 退勤しました。勤務時間: **{format_time(diff)}**", ephemeral=True)

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="g_craft")
    async def craft_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name FROM products")).fetchall()
        if not prods: return await interaction.response.send_message("❌ 商品データがありません。", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="作った商品を選択")
        for p in prods: s.add_option(label=p[0], value=p[0])
        async def cb(i):
            m = discord.ui.Modal(title="制作数入力"); q = discord.ui.TextInput(label="個数", default="1"); m.add_item(q)
            async def scb(mi):
                qty = int(q.value); pn = s.values[0]
                async with aiosqlite.connect(DB_PATH) as db:
                    recipe = await (await db.execute("SELECT material_name, quantity FROM recipes WHERE product_name=?", (pn,))).fetchall()
                    if not recipe: return await mi.response.send_message("⚠️ レシピが設定されていません。", ephemeral=True)
                    for mn, mq in recipe:
                        await db.execute("UPDATE materials SET current = current - ? WHERE name=?", (mq * qty, mn))
                        await check_alerts(mn, "material")
                    await db.execute("UPDATE products SET current = current + ? WHERE name=?", (qty, pn))
                    await db.commit()
                await add_audit(mi.user.id, "CRAFT", f"{pn}x{qty}")
                await mi.response.send_message(f"✅ {pn} を {qty}個制作し、在庫を更新しました。", ephemeral=True)
            m.on_submit = scb; await i.response.send_modal(m)
        s.callback = cb; v.add_item(s); await interaction.response.send_message("何を作りましたか？", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上登録", style=discord.ButtonStyle.secondary, custom_id="g_sale")
    async def sale_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name, price FROM products")).fetchall()
        v = discord.ui.View(); s = discord.ui.Select(placeholder="販売した商品を選択")
        for p in prods: s.add_option(label=f"{p[0]} ({p[1]}{CURRENCY})", value=f"{p[0]}:{p[1]}")
        async def cb(i):
            pn, pr = s.values[0].split(":"); m = discord.ui.Modal(title="販売数"); q = discord.ui.TextInput(label="個数", default="1"); m.add_item(q)
            async def scb(mi):
                total = int(q.value) * int(pr)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE products SET current = current - ? WHERE name=?", (int(q.value), pn))
                    await db.execute("INSERT INTO sales_ranking VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET total_amount = total_amount + ?", (mi.user.id, total, total))
                    await db.commit()
                    await check_alerts(pn, "product")
                await add_audit(mi.user.id, "SALE", f"{pn}x{q.value}")
                await mi.response.send_message(f"💰 {total}{CURRENCY} の売上を登録しました。商品在庫が減少しました。", ephemeral=True)
            m.on_submit = scb; await i.response.send_modal(m)
        s.callback = cb; v.add_item(s); await interaction.response.send_message("何を売りましたか？", view=v, ephemeral=True)

class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="👤 メンバー管理", style=discord.ButtonStyle.success, custom_id="a_role_v3")
    async def role_mgmt(self, interaction, button):
        v = discord.ui.View(); s = discord.ui.Select(placeholder="ロールを選択してください")
        for name, rid in ROLE_OPTIONS.items(): s.add_option(label=name, value=str(rid))
        async def sel_cb(i):
            rid = int(s.values[0]); v2 = discord.ui.View()
            b1 = discord.ui.Button(label="➕ 付与", style=discord.ButtonStyle.success)
            b1.callback = lambda i2: i2.response.send_modal(RoleActionModal(rid, "付与"))
            b2 = discord.ui.Button(label="➖ 削除", style=discord.ButtonStyle.danger)
            b2.callback = lambda i2: i2.response.send_modal(RoleActionModal(rid, "削除"))
            v2.add_item(b1).add_item(b2); await i.response.send_message(f"選択したロールに対して操作を選んでください。", view=v2, ephemeral=True)
        s.callback = sel_cb; v.add_item(s); await interaction.response.send_message("メンバー管理メニュー", view=v, ephemeral=True)

    @discord.ui.button(label="🏆 ランキング", style=discord.ButtonStyle.success, custom_id="a_rank")
    async def view_rank(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
        txt = "🏆 **売上ランキング**\n"
        for i, r in enumerate(rows, 1):
            m = interaction.guild.get_member(r[0]); name = m.display_name if m else f"ID:{r[0]}"
            txt += f"{i}位: {name} - **{r[1]}{CURRENCY}**\n"
        v = discord.ui.View()
        b_all = discord.ui.Button(label="全体リセット", style=discord.ButtonStyle.danger)
        async def r_all(i):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM sales_ranking"); await db.commit()
            await i.response.send_message("✅ 全ランキングをリセットしました。", ephemeral=True)
        b_all.callback = r_all; v.add_item(b_all)
        b_ind = discord.ui.Button(label="個人リセット", style=discord.ButtonStyle.secondary)
        async def r_ind1(i):
            v2 = discord.ui.View(); s2 = discord.ui.Select(placeholder="リセットする人を選択")
            for r in rows: s2.add_option(label=f"ID:{r[0]}", value=str(r[0]))
            async def r_ind2(i2):
                async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM sales_ranking WHERE user_id=?", (int(s2.values[0]),)); await db.commit()
                await i2.response.send_message("✅ 個人データをリセットしました。", ephemeral=True)
            s2.callback = r_ind2; v2.add_item(s2); await i.response.send_message("対象を選択してください：", view=v2, ephemeral=True)
        b_ind.callback = r_ind1; v.add_item(b_ind)
        await interaction.response.send_message(txt or "データなし", view=v, ephemeral=True)

    @discord.ui.button(label="⏰ 集計", style=discord.ButtonStyle.primary, custom_id="a_sum")
    async def work_sum(self, interaction, button):
        bonus = int(await get_config("hourly_bonus", "0"))
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT user_id, SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE end IS NOT NULL GROUP BY user_id")).fetchall()
        txt = f"📊 **勤務集計 (時給:{bonus}{CURRENCY})**\n"
        for u_id, sec in rows:
            m = interaction.guild.get_member(u_id); n = m.display_name if m else f"ID:{u_id}"
            pay = int((sec/3600)*bonus)
            txt += f"👤 {n}: **{format_time(sec)}** (報酬計: {pay}{CURRENCY})\n"
        await interaction.response.send_message(txt or "データなし", ephemeral=True)

    @discord.ui.button(label="📋 監査ログ", style=discord.ButtonStyle.gray, custom_id="a_audit")
    async def view_audit(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, user_id, action, detail FROM audit_logs ORDER BY id DESC LIMIT 15")).fetchall()
        txt = "📜 **監査ログ (最新15件)**\n```"
        for r in rows: txt += f"[{r[0][5:16]}] ID:{r[1]} | {r[2]} | {r[3]}\n"
        await interaction.response.send_message(txt + "```", ephemeral=True)

    @discord.ui.button(label="💰 ボーナス設定", style=discord.ButtonStyle.secondary, custom_id="a_bonus")
    async def bonus_btn(self, interaction, button):
        await interaction.response.send_modal(BonusSetModal())

    @discord.ui.button(label="📦 在庫・レシピ管理", style=discord.ButtonStyle.gray, custom_id="a_stock")
    async def stock_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            mats = await (await db.execute("SELECT name, current, threshold FROM materials")).fetchall()
            prods = await (await db.execute("SELECT name, current, threshold FROM products")).fetchall()
        txt = "📦 **現在在庫 (現在/目標)**\n**素材**: " + ", ".join([f"{m[0]}({m[1]}/{m[2]})" for m in mats]) + "\n**商品**: " + ", ".join([f"{p[0]}({p[1]}/{p[2]})" for p in prods])
        v = discord.ui.View()
        v.add_item(discord.ui.Button(label="商品登録", style=discord.ButtonStyle.success)).callback = lambda i: i.response.send_modal(ProductDefineModal())
        v.add_item(discord.ui.Button(label="素材登録", style=discord.ButtonStyle.secondary)).callback = lambda i: i.response.send_modal(MaterialAddModal())
        if prods and mats:
            s = discord.ui.Select(placeholder="レシピ設定する商品を選択")
            for p in prods: s.add_option(label=p[0], value=p[0])
            async def r_cb1(i):
                v2 = discord.ui.View(); s2 = discord.ui.Select(placeholder="使用する素材を選択")
                for m in mats: s2.add_option(label=m[0], value=m[0])
                s2.callback = lambda i2: i2.response.send_modal(RecipeSetModal(s.values[0], s2.values[0]))
                v2.add_item(s2); await i.response.send_message(f"素材を選んでください", view=v2, ephemeral=True)
            s.callback = r_cb1; v.add_item(s)
        await interaction.response.send_message(txt or "データなし", view=v, ephemeral=True)

# ================= 5. メインロジック =================

async def refresh_panels():
    channels = [(ADMIN_PANEL_CHANNEL_ID, AdminPanel(), "🔧 **管理者パネル**"), (GENERAL_PANEL_CHANNEL_ID, GeneralPanel(), "🧾 **業務パネル**")]
    for c_id, view, content in channels:
        channel = bot.get_channel(c_id)
        if channel:
            try:
                await channel.purge(limit=10)
                await channel.send(content, view=view)
            except: pass

@bot.event
async def on_ready():
    await init_db()
    bot.add_view(GeneralPanel()); bot.add_view(AdminPanel())
    print(f"Logged in: {bot.user}")
    await refresh_panels()

@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    await refresh_panels()
    await ctx.send("✅ パネルを再配置しました。")

if TOKEN: bot.run(TOKEN)
