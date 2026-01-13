import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime
import os
from dotenv import load_dotenv

# ================= 1. 設定 =================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

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

# ================= 2. DB初期化 & 共通関数 =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS work_logs(user_id INTEGER, start DATETIME, end DATETIME);
        CREATE TABLE IF NOT EXISTS materials(name TEXT PRIMARY KEY, current INTEGER DEFAULT 0, threshold INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS products(name TEXT PRIMARY KEY, price INTEGER DEFAULT 0, current INTEGER DEFAULT 0, threshold INTEGER DEFAULT 0);
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

def format_time(seconds):
    h, m = int(seconds // 3600), int((seconds % 3600) // 60)
    return f"{h}時間{m}分"

# ================= 3. モーダル類 =================

class SimpleInputModal(discord.ui.Modal):
    def __init__(self, title, label, callback_func):
        super().__init__(title=title)
        self.input = discord.ui.TextInput(label=label)
        self.add_item(self.input)
        self.callback_func = callback_func
    async def on_submit(self, interaction):
        await self.callback_func(interaction, self.input.value)

class ItemAddModal(discord.ui.Modal):
    def __init__(self, mode):
        super().__init__(title="商品登録" if mode == "prod" else "素材登録")
        self.mode = mode
        self.name_in = discord.ui.TextInput(label="名前")
        self.threshold_in = discord.ui.TextInput(label="下限アラート数", default="5")
        self.add_item(self.name_in); self.add_item(self.threshold_in)
        if mode == "prod":
            self.price_in = discord.ui.TextInput(label="販売価格", default="0")
            self.add_item(self.price_in)
    async def on_submit(self, interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            if self.mode == "prod":
                await db.execute("INSERT OR REPLACE INTO products (name, price, threshold, current) VALUES (?,?,?,COALESCE((SELECT current FROM products WHERE name=?),0))", 
                                (self.name_in.value, int(self.price_in.value), int(self.threshold_in.value), self.name_in.value))
            else:
                await db.execute("INSERT OR REPLACE INTO materials (name, threshold, current) VALUES (?,?,COALESCE((SELECT current FROM materials WHERE name=?),0))", 
                                (self.name_in.value, int(self.threshold_in.value), self.name_in.value))
            await db.commit()
        await add_audit(interaction.user.id, f"ADD_{self.mode.upper()}", self.name_in.value)
        await interaction.response.send_message(f"✅ {self.name_in.value} を保存しました。", ephemeral=True)

class StockAdjustModal(discord.ui.Modal):
    def __init__(self, name, table, mode):
        super().__init__(title=f"{name} の{'補充' if mode == 'add' else '引き出し'}")
        self.name, self.table, self.mode = name, table, mode
        self.qty = discord.ui.TextInput(label="個数")
        self.add_item(self.qty)
    async def on_submit(self, interaction):
        val = int(self.qty.value) * (1 if self.mode == 'add' else -1)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(f"UPDATE {self.table} SET current = current + ? WHERE name = ?", (val, self.name))
            await db.commit()
        await add_audit(interaction.user.id, "STOCK_ADJ", f"{self.name}: {val}")
        await interaction.response.send_message(f"✅ 在庫を更新しました。", ephemeral=True)

# ================= 4. 管理パネル =================

class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="👤 メンバー管理", style=discord.ButtonStyle.success, custom_id="adm_mem_v3")
    async def member_mgmt(self, interaction, button):
        v = discord.ui.View(); s = discord.ui.Select(placeholder="ロールを選択")
        for n, rid in ROLE_OPTIONS.items(): s.add_option(label=n, value=str(rid))
        async def scb(i):
            rid = int(s.values[0]); v2 = discord.ui.View()
            async def role_cb(i2, uid):
                m = i2.guild.get_member(int(uid))
                r = i2.guild.get_role(rid)
                if "付与" in i2.data['custom_id']: await m.add_roles(r)
                else: await m.remove_roles(r)
                await i2.response.send_message("✅ ロールを更新しました。", ephemeral=True)
            b1 = discord.ui.Button(label="付与", style=discord.ButtonStyle.success, custom_id="role_add")
            b1.callback = lambda i2: i2.response.send_modal(SimpleInputModal("ロール付与", "ユーザーID", role_cb))
            b2 = discord.ui.Button(label="削除", style=discord.ButtonStyle.danger, custom_id="role_rem")
            b2.callback = lambda i2: i2.response.send_modal(SimpleInputModal("ロール削除", "ユーザーID", role_cb))
            v2.add_item(b1).add_item(b2); await i.response.send_message("操作:", view=v2, ephemeral=True)
        s.callback = scb; v.add_item(s); await interaction.response.send_message("メンバー管理:", view=v, ephemeral=True)

    @discord.ui.button(label="📦 在庫管理", style=discord.ButtonStyle.secondary, custom_id="adm_stock_v3")
    async def stock_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            mats = await (await db.execute("SELECT name, current FROM materials")).fetchall()
            prods = await (await db.execute("SELECT name, current FROM products")).fetchall()
        txt = "📦 **在庫一覧**\n" + "\n".join([f"・{m[0]}: {m[1]}" for m in mats + prods])
        v = discord.ui.View(); s = discord.ui.Select(placeholder="アイテムを選択")
        for m in mats: s.add_option(label=f"素材: {m[0]}", value=f"materials:{m[0]}")
        for p in prods: s.add_option(label=f"商品: {p[0]}", value=f"products:{p[0]}")
        async def scb(i):
            tbl, name = s.values[0].split(":"); v2 = discord.ui.View()
            b1 = discord.ui.Button(label="➕ 補充", style=discord.ButtonStyle.success)
            b1.callback = lambda i2: i2.response.send_modal(StockAdjustModal(name, tbl, "add"))
            b2 = discord.ui.Button(label="➖ 引出", style=discord.ButtonStyle.danger)
            b2.callback = lambda i2: i2.response.send_modal(StockAdjustModal(name, tbl, "sub"))
            v2.add_item(b1).add_item(b2); await i.response.send_message(f"**{name}**:", view=v2, ephemeral=True)
        s.callback = scb; v.add_item(s); await interaction.response.send_message(txt, view=v, ephemeral=True)

    @discord.ui.button(label="📜 レシピ・登録管理", style=discord.ButtonStyle.primary, custom_id="adm_recipe_v3")
    async def recipe_mgmt(self, interaction, button):
        v = discord.ui.View()
        b1 = discord.ui.Button(label="➕ 商品追加", style=discord.ButtonStyle.success)
        b1.callback = lambda i: i.response.send_modal(ItemAddModal("prod"))
        b2 = discord.ui.Button(label="➕ 素材追加", style=discord.ButtonStyle.success)
        b2.callback = lambda i: i.response.send_modal(ItemAddModal("mat"))
        v.add_item(b1).add_item(b2)
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name FROM products")).fetchall()
            mats = await (await db.execute("SELECT name FROM materials")).fetchall()
        if prods or mats:
            sd = discord.ui.Select(placeholder="🗑️ 削除する")
            for p in prods: sd.add_option(label=f"商品: {p[0]}", value=f"products:{p[0]}")
            for m in mats: sd.add_option(label=f"素材: {m[0]}", value=f"materials:{m[0]}")
            async def dcb(i):
                t, n = sd.values[0].split(":")
                async with aiosqlite.connect(DB_PATH) as db: await db.execute(f"DELETE FROM {t} WHERE name=?", (n,)); await db.commit()
                await i.response.send_message(f"✅ {n} を削除しました。", ephemeral=True)
            sd.callback = dcb; v.add_item(sd)
        await interaction.response.send_message("登録・削除:", view=v, ephemeral=True)

    @discord.ui.button(label="🏆 ランキング", style=discord.ButtonStyle.success, custom_id="adm_rank_v3")
    async def view_rank(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
        txt = "🏆 **売上ランキング**\n" + "\n".join([f"第{i+1}位: <@{r[0]}> - {r[1]}{CURRENCY}" for i, r in enumerate(rows)])
        v = discord.ui.View(); br = discord.ui.Button(label="🔄 リセット", style=discord.ButtonStyle.danger)
        async def rcb(i):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM sales_ranking"); await db.commit()
            await i.response.send_message("✅ リセット完了", ephemeral=True)
        br.callback = rcb; v.add_item(br); await interaction.response.send_message(txt or "データなし", view=v, ephemeral=True)

    @discord.ui.button(label="⏰ 集計・時給", style=discord.ButtonStyle.primary, custom_id="adm_sum_v3")
    async def work_sum(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT user_id, SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE end IS NOT NULL GROUP BY user_id")).fetchall()
            bonus = await (await db.execute("SELECT value FROM config WHERE key='hourly_bonus'")).fetchone()
        rate = int(bonus[0] if bonus else 0)
        txt = "📊 **勤務集計 (時給: {rate}{CURRENCY})**\n"
        for r in rows:
            hours = r[1] / 3600
            txt += f"<@{r[0]}>: {format_time(r[1])} (見込給与: {int(hours * rate)}{CURRENCY})\n"
        v = discord.ui.View(); bb = discord.ui.Button(label="💰 時給設定", style=discord.ButtonStyle.gray)
        async def bcb(i, val):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR REPLACE INTO config VALUES ('hourly_bonus', ?)", (val,)); await db.commit()
            await i.response.send_message(f"✅ 時給を {val} に設定しました。", ephemeral=True)
        bb.callback = lambda i: i.response.send_modal(SimpleInputModal("時給設定", "金額を入力", bcb))
        v.add_item(bb); await interaction.response.send_message(txt or "集計データなし", view=v, ephemeral=True)

    @discord.ui.button(label="📜 履歴ログ", style=discord.ButtonStyle.gray, custom_id="adm_audit_v3")
    async def view_audit(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, action, detail FROM audit_logs ORDER BY id DESC LIMIT 10")).fetchall()
        txt = "📜 **直近10件の履歴**\n```" + "\n".join([f"[{r[0][5:16]}] {r[1]}: {r[2]}" for r in rows]) + "```"
        await interaction.response.send_message(txt if rows else "履歴なし", ephemeral=True)

# ================= 5. 業務パネル =================

class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🟢 出勤", style=discord.ButtonStyle.success, custom_id="gen_in_v3")
    async def in_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT INTO work_logs VALUES (?,?,NULL)", (interaction.user.id, datetime.now())); await db.commit()
        await interaction.user.add_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message("🟢 出勤しました。", ephemeral=True)

    @discord.ui.button(label="🔴 退勤", style=discord.ButtonStyle.danger, custom_id="gen_out_v3")
    async def out_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT start FROM work_logs WHERE user_id=? AND end IS NULL", (interaction.user.id,))
            row = await cur.fetchone()
            if not row: return await interaction.response.send_message("❌ 出勤記録なし", ephemeral=True)
            duration = (datetime.now() - datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S.%f")).total_seconds()
            await db.execute("UPDATE work_logs SET end=? WHERE user_id=? AND end IS NULL", (datetime.now(), interaction.user.id)); await db.commit()
        await interaction.user.remove_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message(f"🔴 退勤しました。({format_time(duration)})", ephemeral=True)

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="gen_craft_v3")
    async def craft_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name FROM products")).fetchall()
        v = discord.ui.View(); s = discord.ui.Select(placeholder="制作物")
        for p in prods: s.add_option(label=p[0], value=p[0])
        async def cb(i, qty):
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE products SET current = current + ? WHERE name=?", (int(qty), s.values[0]))
                await db.commit()
            await i.response.send_message(f"✅ {s.values[0]} を {qty} 個制作登録しました。", ephemeral=True)
        s.callback = lambda i: i.response.send_modal(SimpleInputModal("制作数", "個数", cb))
        v.add_item(s); await interaction.response.send_message("制作報告:", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上登録", style=discord.ButtonStyle.secondary, custom_id="gen_sale_v3")
    async def sale_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name, price FROM products")).fetchall()
        v = discord.ui.View(); s = discord.ui.Select(placeholder="販売物")
        for p in prods: s.add_option(label=f"{p[0]} ({p[1]}{CURRENCY})", value=f"{p[0]}:{p[1]}")
        async def cb(i, qty):
            pn, pr = s.values[0].split(":"); total = int(qty) * int(pr)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO sales_ranking (user_id, total_amount) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET total_amount = total_amount + ?", (i.user.id, total, total))
                await db.commit()
            await i.response.send_message(f"💰 {total}{CURRENCY} の売上を登録しました。", ephemeral=True)
        s.callback = lambda i: i.response.send_modal(SimpleInputModal("販売数", "個数", cb))
        v.add_item(s); await interaction.response.send_message("売上登録:", view=v, ephemeral=True)

# ================= 6. 起動 =================
@bot.event
async def on_ready():
    await init_db(); bot.add_view(AdminPanel()); bot.add_view(GeneralPanel())
    for c_id, view, txt in [(ADMIN_PANEL_CHANNEL_ID, AdminPanel(), "🔧 **管理パネル**"), (GENERAL_PANEL_CHANNEL_ID, GeneralPanel(), "🧾 **業務パネル**")]:
        ch = bot.get_channel(c_id)
        if ch: await ch.purge(limit=5); await ch.send(txt, view=view)
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)
