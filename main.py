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

OMNIS_ROLE_ID = 1459208662055911538
WORK_ROLE_ID = 1459209336076374068
ADMIN_ROLE_ID = 1459388566760325318

ADMIN_PANEL_CHANNEL_ID = 1459371812310745171
GENERAL_PANEL_CHANNEL_ID = 1458801073899966585
ALERT_CHANNEL_ID = 1459388566760325318 

DB_PATH = "data.db"
CURRENCY = "円"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= 2. DB初期化 =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(f"""
        CREATE TABLE IF NOT EXISTS work_logs(user_id INTEGER, start DATETIME, end DATETIME);
        CREATE TABLE IF NOT EXISTS materials(name TEXT PRIMARY KEY, current INTEGER DEFAULT 0, threshold INTEGER DEFAULT 0);
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

async def check_alerts(item_name, item_type="material"):
    async with aiosqlite.connect(DB_PATH) as db:
        table = "materials" if item_type == "material" else "products"
        cur = await db.execute(f"SELECT current, threshold FROM {table} WHERE name=?", (item_name,))
        row = await cur.fetchone()
        if row and row[0] < row[1]:
            channel = bot.get_channel(ALERT_CHANNEL_ID)
            if channel:
                await channel.send(f"⚠️ **【在庫不足】** {item_type == 'material' and '素材' or '商品'}「{item_name}」が目標を下回りました (現在:{row[0]})")

def format_time(seconds):
    return f"{int(seconds // 3600)}時間{int((seconds % 3600) // 60)}分"

# ================= 3. モーダル類 =================

class RecipeSetModal(discord.ui.Modal):
    def __init__(self, p_name, m_name):
        super().__init__(title=f"レシピ: {p_name}")
        self.p_name, self.m_name = p_name, m_name
        self.qty = discord.ui.TextInput(label=f"{m_name} の必要個数", default="1")
        self.add_item(self.qty)
    async def on_submit(self, interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (self.p_name, self.m_name, int(self.qty.value)))
            await db.commit()
        await interaction.response.send_message(f"✅ レシピ登録: {self.p_name} 制作時に {self.m_name} を {self.qty.value} 個消費します。", ephemeral=True)

class ProductDefineModal(discord.ui.Modal, title="商品登録"):
    name = discord.ui.TextInput(label="商品名")
    price = discord.ui.TextInput(label="販売価格")
    threshold = discord.ui.TextInput(label="目標在庫(アラート)", default="5")
    async def on_submit(self, interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO products (name, price, current, threshold) VALUES (?,?,?,?)", (self.name.value, int(self.price.value), 0, int(self.threshold.value)))
            await db.commit()
        await interaction.response.send_message(f"✅ 商品 {self.name.value} を登録しました。", ephemeral=True)

class MaterialAddModal(discord.ui.Modal, title="素材登録"):
    name = discord.ui.TextInput(label="素材名")
    threshold = discord.ui.TextInput(label="目標在庫(アラート)", default="10")
    async def on_submit(self, interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO materials (name, current, threshold) VALUES (?, 0, ?)", (self.name.value, int(self.threshold.value)))
            await db.commit()
        await interaction.response.send_message(f"✅ 素材 {self.name.value} を登録しました。", ephemeral=True)

class RoleActionModal(discord.ui.Modal):
    def __init__(self, role_id, mode):
        super().__init__(title="ロール操作")
        self.role_id, self.mode = role_id, mode
        self.uid = discord.ui.TextInput(label="ユーザーID")
        self.add_item(self.uid)
    async def on_submit(self, interaction):
        member = interaction.guild.get_member(int(self.uid.value))
        role = interaction.guild.get_role(self.role_id)
        if self.mode == "add": await member.add_roles(role)
        else: await member.remove_roles(role)
        await interaction.response.send_message(f"✅ {member.display_name} に {role.name} を{'付与' if self.mode=='add' else '解除'}しました。", ephemeral=True)

# ================= 4. 管理パネル (修正版) =================

class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="👤 メンバー管理", style=discord.ButtonStyle.success, custom_id="adm_role")
    async def role_mgmt(self, interaction, button):
        v = discord.ui.View(); s = discord.ui.Select(placeholder="ロール選択")
        for n, rid in ROLE_OPTIONS.items(): s.add_option(label=n, value=str(rid))
        async def scb(i):
            rid = int(s.values[0]); v2 = discord.ui.View()
            b1 = discord.ui.Button(label="付与", style=discord.ButtonStyle.success)
            b1.callback = lambda i2: i2.response.send_modal(RoleActionModal(rid, "add"))
            b2 = discord.ui.Button(label="解除", style=discord.ButtonStyle.danger)
            b2.callback = lambda i2: i2.response.send_modal(RoleActionModal(rid, "rem"))
            v2.add_item(b1).add_item(b2); await i.response.send_message("操作を選んでください", view=v2, ephemeral=True)
        s.callback = scb; v.add_item(s); await interaction.response.send_message("管理ロール選択:", view=v, ephemeral=True)

    @discord.ui.button(label="🏆 ランキング", style=discord.ButtonStyle.primary, custom_id="adm_rank")
    async def view_rank(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
        
        txt = "🏆 **売上ランキング**\n" + "\n".join([f"{i+1}位: <@{r[0]}> - {r[1]}{CURRENCY}" for i, r in enumerate(rows)])
        v = discord.ui.View()
        # 全体リセット
        b_all = discord.ui.Button(label="全リセット", style=discord.ButtonStyle.danger)
        async def r_all(i):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM sales_ranking"); await db.commit()
            await i.response.send_message("✅ ランキングをリセットしました。", ephemeral=True)
        b_all.callback = r_all; v.add_item(b_all)
        # 個人リセット (修正)
        if rows:
            s_ind = discord.ui.Select(placeholder="個人データを削除")
            for r in rows:
                m = interaction.guild.get_member(r[0]); n = m.display_name if m else f"ID:{r[0]}"
                s_ind.add_option(label=n, value=str(r[0]))
            async def r_ind(i):
                async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM sales_ranking WHERE user_id=?", (int(s_ind.values[0]),)); await db.commit()
                await i.response.send_message("✅ 削除しました。", ephemeral=True)
            s_ind.callback = r_ind; v.add_item(s_ind)
        await interaction.response.send_message(txt or "データなし", view=v, ephemeral=True)

    @discord.ui.button(label="⏰ 集計", style=discord.ButtonStyle.primary, custom_id="adm_sum")
    async def work_sum(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            bonus = int(await (await db.execute("SELECT value FROM config WHERE key='hourly_bonus'")).fetchone() or [0])[0]
            rows = await (await db.execute("SELECT user_id, SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE end IS NOT NULL GROUP BY user_id")).fetchall()
        
        txt = f"📊 **勤務集計 (時給:{bonus}{CURRENCY})**\n"
        v = discord.ui.View()
        # 全リセット
        b_all = discord.ui.Button(label="全リセット", style=discord.ButtonStyle.danger)
        async def r_all(i):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM work_logs"); await db.commit()
            await i.response.send_message("✅ 勤務記録をリセットしました。", ephemeral=True)
        b_all.callback = r_all; v.add_item(b_all)
        # 個人リセット
        if rows:
            s_ind = discord.ui.Select(placeholder="個別にリセット")
            for u_id, sec in rows:
                m = interaction.guild.get_member(u_id); n = m.display_name if m else f"ID:{u_id}"
                txt += f"👤 {n}: {format_time(sec)} ({int((sec/3600)*bonus)}{CURRENCY})\n"
                s_ind.add_option(label=n, value=str(u_id))
            async def r_ind(i):
                async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM work_logs WHERE user_id=?", (int(s_ind.values[0]),)); await db.commit()
                await i.response.send_message("✅ 個別集計を削除しました。", ephemeral=True)
            s_ind.callback = r_ind; v.add_item(s_ind)
        await interaction.response.send_message(txt, view=v, ephemeral=True)

    @discord.ui.button(label="📦 在庫・レシピ管理", style=discord.ButtonStyle.secondary, custom_id="adm_stock")
    async def stock_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            mats = await (await db.execute("SELECT name, current, threshold FROM materials")).fetchall()
            prods = await (await db.execute("SELECT name, current, threshold FROM products")).fetchall()
        
        txt = "📦 **現在在庫**\n"
        txt += "【素材】: " + ", ".join([f"{m[0]}({m[1]})" for m in mats]) + "\n"
        txt += "【商品】: " + ", ".join([f"{p[0]}({p[1]})" for p in prods])
        
        v = discord.ui.View()
        b1 = discord.ui.Button(label="商品追加", style=discord.ButtonStyle.success)
        b1.callback = lambda i: i.response.send_modal(ProductDefineModal())
        b2 = discord.ui.Button(label="素材追加", style=discord.ButtonStyle.success)
        b2.callback = lambda i: i.response.send_modal(MaterialAddModal())
        v.add_item(b1).add_item(b2)
        
        if prods and mats:
            s1 = discord.ui.Select(placeholder="レシピ設定: 商品を選択")
            for p in prods: s1.add_option(label=p[0], value=p[0])
            async def s1_cb(i):
                p_name = s1.values[0]; v2 = discord.ui.View()
                s2 = discord.ui.Select(placeholder=f"使用する素材を選択")
                for m in mats: s2.add_option(label=m[0], value=m[0])
                async def s2_cb(i2): await i2.response.send_modal(RecipeSetModal(p_name, s2.values[0]))
                s2.callback = s2_cb; v2.add_item(s2)
                await i.response.send_message(f"「{p_name}」の素材を選択してください:", view=v2, ephemeral=True)
            s1.callback = s1_cb; v.add_item(s1)
        await interaction.response.send_message(txt, view=v, ephemeral=True)

# ================= 5. 業務パネル =================

class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🟢 出勤", style=discord.ButtonStyle.success, custom_id="gen_in")
    async def in_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO work_logs VALUES (?,?,NULL)", (interaction.user.id, datetime.now()))
            await db.commit()
        await interaction.user.add_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message("🟢 出勤しました", ephemeral=True)

    @discord.ui.button(label="🔴 退勤", style=discord.ButtonStyle.danger, custom_id="gen_out")
    async def out_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            row = await (await db.execute("SELECT rowid, start FROM work_logs WHERE user_id=? AND end IS NULL", (interaction.user.id,))).fetchone()
            if not row: return await interaction.response.send_message("❌ 出勤記録がありません", ephemeral=True)
            end_t = datetime.now()
            await db.execute("UPDATE work_logs SET end=? WHERE rowid=?", (end_t, row[0]))
            await db.commit()
        await interaction.user.remove_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message("🔴 退勤しました", ephemeral=True)

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="gen_craft")
    async def craft_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name FROM products")).fetchall()
        if not prods: return await interaction.response.send_message("❌ 商品がありません", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="制作した商品")
        for p in prods: s.add_option(label=p[0], value=p[0])
        async def cb(i):
            modal = discord.ui.Modal(title="制作数"); q = discord.ui.TextInput(label="個数", default="1"); modal.add_item(q)
            async def scb(mi):
                qty, pn = int(q.value), s.values[0]
                async with aiosqlite.connect(DB_PATH) as db:
                    recipe = await (await db.execute("SELECT material_name, quantity FROM recipes WHERE product_name=?", (pn,))).fetchall()
                    for mn, mq in recipe:
                        await db.execute("UPDATE materials SET current = current - ? WHERE name=?", (mq*qty, mn))
                        await check_alerts(mn, "material")
                    await db.execute("UPDATE products SET current = current + ? WHERE name=?", (qty, pn))
                    await db.commit()
                await mi.response.send_message(f"✅ {pn} を {qty} 個制作登録しました", ephemeral=True)
            modal.on_submit = scb; await i.response.send_modal(modal)
        s.callback = cb; v.add_item(s); await interaction.response.send_message("制作報告:", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上登録", style=discord.ButtonStyle.secondary, custom_id="gen_sale")
    async def sale_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name, price FROM products")).fetchall()
        if not prods: return await interaction.response.send_message("❌ 商品がありません", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="販売した商品")
        for p in prods: s.add_option(label=f"{p[0]} ({p[1]}{CURRENCY})", value=f"{p[0]}:{p[1]}")
        async def cb(i):
            pn, pr = s.values[0].split(":"); modal = discord.ui.Modal(title="販売数"); q = discord.ui.TextInput(label="個数", default="1"); modal.add_item(q)
            async def scb(mi):
                total = int(q.value) * int(pr)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE products SET current = current - ? WHERE name=?", (int(q.value), pn))
                    await db.execute("INSERT INTO sales_ranking (user_id, total_amount) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET total_amount=total_amount+?", (mi.user.id, total, total))
                    await db.commit()
                    await check_alerts(pn, "product")
                await mi.response.send_message(f"💰 {total}{CURRENCY} の売上を登録しました", ephemeral=True)
            modal.on_submit = scb; await i.response.send_modal(modal)
        s.callback = cb; v.add_item(s); await interaction.response.send_message("売上登録:", view=v, ephemeral=True)

# ================= 6. 起動 =================

@bot.event
async def on_ready():
    await init_db()
    bot.add_view(AdminPanel())
    bot.add_view(GeneralPanel())
    print(f"Logged in: {bot.user}")
    # チャンネルにパネルを送信
    for c_id, view, txt in [(ADMIN_PANEL_CHANNEL_ID, AdminPanel(), "🔧 管理者パネル"), (GENERAL_PANEL_CHANNEL_ID, GeneralPanel(), "🧾 業務パネル")]:
        ch = bot.get_channel(c_id)
        if ch: 
            await ch.purge(limit=5)
            await ch.send(txt, view=view)

bot.run(TOKEN)
