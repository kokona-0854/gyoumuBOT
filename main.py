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

# ================= 2. DB初期化 & 共通関数 =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(f"""
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
    return f"{int(seconds // 3600)}時間{int((seconds % 3600) // 60)}分"

# ================= 3. モーダル類 =================

class RoleActionModal(discord.ui.Modal):
    def __init__(self, role_id, mode_label):
        super().__init__(title=f"ロール{mode_label}")
        self.role_id, self.mode_label = role_id, mode_label
        self.uid_input = discord.ui.TextInput(label="対象のユーザーID")
        self.add_item(self.uid_input)
    async def on_submit(self, interaction):
        member = interaction.guild.get_member(int(self.uid_input.value))
        role = interaction.guild.get_role(self.role_id)
        if "付与" in self.mode_label: await member.add_roles(role)
        else: await member.remove_roles(role)
        await add_audit(interaction.user.id, "ROLE_CHANGE", f"{member.display_name} -> {role.name} ({self.mode_label})")
        await interaction.response.send_message(f"✅ ロールを更新しました。", ephemeral=True)

class ItemAddModal(discord.ui.Modal):
    def __init__(self, mode):
        super().__init__(title="新規商品登録" if mode == "prod" else "新規素材登録")
        self.mode = mode
        self.name = discord.ui.TextInput(label="名前")
        self.threshold = discord.ui.TextInput(label="アラート閾値(在庫目標)", default="5")
        self.add_item(self.name)
        self.add_item(self.threshold)
        if mode == "prod":
            self.price = discord.ui.TextInput(label="販売価格", default="0")
            self.add_item(self.price)
    async def on_submit(self, interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            if self.mode == "prod":
                await db.execute("INSERT OR REPLACE INTO products (name, price, threshold) VALUES (?,?,?)", (self.name.value, int(self.price.value), int(self.threshold.value)))
            else:
                await db.execute("INSERT OR REPLACE INTO materials (name, threshold) VALUES (?,?)", (self.name.value, int(self.threshold.value)))
            await db.commit()
        await add_audit(interaction.user.id, f"{self.mode.upper()}_ADD", self.name.value)
        await interaction.response.send_message(f"✅ 「{self.name.value}」を登録しました。", ephemeral=True)

class StockEditModal(discord.ui.Modal):
    def __init__(self, name, table):
        super().__init__(title=f"在庫数修正: {name}")
        self.name, self.table = name, table
        self.qty = discord.ui.TextInput(label="現在の正確な在庫数を入力")
        self.add_item(self.qty)
    async def on_submit(self, interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(f"UPDATE {self.table} SET current = ? WHERE name = ?", (int(self.qty.value), self.name))
            await db.commit()
        await add_audit(interaction.user.id, "STOCK_FIX", f"{self.name} -> {self.qty.value}")
        await interaction.response.send_message(f"✅ 在庫を更新しました。", ephemeral=True)

# ================= 4. 管理者パネル (全機能統合) =================

class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="👤 メンバー管理", style=discord.ButtonStyle.success, custom_id="adm_member")
    async def member_mgmt(self, interaction, button):
        v = discord.ui.View(); s = discord.ui.Select(placeholder="ロールを選択")
        for n, rid in ROLE_OPTIONS.items(): s.add_option(label=n, value=str(rid))
        async def scb(i):
            rid = int(s.values[0]); v2 = discord.ui.View()
            b1 = discord.ui.Button(label="付与", style=discord.ButtonStyle.success)
            b1.callback = lambda i2: i2.response.send_modal(RoleActionModal(rid, "付与"))
            b2 = discord.ui.Button(label="削除", style=discord.ButtonStyle.danger)
            b2.callback = lambda i2: i2.response.send_modal(RoleActionModal(rid, "削除"))
            v2.add_item(b1).add_item(b2); await i.response.send_message("操作を選択:", view=v2, ephemeral=True)
        s.callback = scb; v.add_item(s); await interaction.response.send_message("管理対象を選択:", view=v, ephemeral=True)

    @discord.ui.button(label="📦 在庫一覧・修正", style=discord.ButtonStyle.secondary, custom_id="adm_stock")
    async def stock_list(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            mats = await (await db.execute("SELECT name, current, threshold FROM materials")).fetchall()
            prods = await (await db.execute("SELECT name, current, threshold FROM products")).fetchall()
        txt = "📦 **現在在庫状況** (名称: 現在数 / 目標)\n\n"
        txt += "**【素材】**\n" + ("\n".join([f"・{m[0]}: `{m[1]}` (目標:{m[2]})" for m in mats]) if mats else "なし") + "\n\n"
        txt += "**【商品】**\n" + ("\n".join([f"・{p[0]}: `{p[1]}` (目標:{p[2]})" for p in prods]) if prods else "なし")
        v = discord.ui.View()
        if mats or prods:
            s = discord.ui.Select(placeholder="個数を修正するアイテムを選択")
            for m in mats: s.add_option(label=f"素材: {m[0]}", value=f"materials:{m[0]}")
            for p in prods: s.add_option(label=f"商品: {p[0]}", value=f"products:{p[0]}")
            async def scb(i): await i.response.send_modal(StockEditModal(s.values[0].split(":")[1], s.values[0].split(":")[0]))
            s.callback = scb; v.add_item(s)
        await interaction.response.send_message(txt, view=v, ephemeral=True)

    @discord.ui.button(label="📜 レシピ・登録管理", style=discord.ButtonStyle.primary, custom_id="adm_recipe")
    async def recipe_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name FROM products")).fetchall()
            mats = await (await db.execute("SELECT name FROM materials")).fetchall()
        v = discord.ui.View()
        v.add_item(discord.ui.Button(label="➕商品追加", style=discord.ButtonStyle.success, custom_id="add_p")).callback = lambda i: i.response.send_modal(ItemAddModal("prod"))
        v.add_item(discord.ui.Button(label="➕素材追加", style=discord.ButtonStyle.success, custom_id="add_m")).callback = lambda i: i.response.send_modal(ItemAddModal("mat"))
        if prods or mats:
            s_del = discord.ui.Select(placeholder="🗑️ 登録を削除する")
            for p in prods: s_del.add_option(label=f"商品削除: {p[0]}", value=f"products:{p[0]}")
            for m in mats: s_del.add_option(label=f"素材削除: {m[0]}", value=f"materials:{m[0]}")
            async def dcb(i):
                tbl, name = s_del.values[0].split(":"); 
                async with aiosqlite.connect(DB_PATH) as db: 
                    await db.execute(f"DELETE FROM {tbl} WHERE name=?", (name,)); await db.commit()
                await i.response.send_message(f"🗑️ {name} を削除しました。", ephemeral=True)
            s_del.callback = dcb; v.add_item(s_del)
        if prods and mats:
            s_rec = discord.ui.Select(placeholder="🛠️ レシピ設定 (複数素材紐付け)")
            for p in prods: s_rec.add_option(label=f"レシピ編集: {p[0]}", value=p[0])
            async def rcb(i):
                p_name = s_rec.values[0]; v2 = discord.ui.View()
                s_mats = discord.ui.Select(placeholder="使用素材を選択(複数可)", min_values=1, max_values=len(mats))
                for m in mats: s_mats.add_option(label=m[0], value=m[0])
                async def s_mats_cb(i2):
                    selected = s_mats.values
                    class QtyModal(discord.ui.Modal, title=f"{p_name} の必要数"):
                        def __init__(self, m_list):
                            super().__init__(); self.m_list = m_list; self.inps = []
                            for n in m_list[:5]:
                                inp = discord.ui.TextInput(label=f"{n} の必要数", default="1"); self.add_item(inp); self.inps.append((n, inp))
                        async def on_submit(self, i3):
                            async with aiosqlite.connect(DB_PATH) as db:
                                for n, inp in self.inps: await db.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (p_name, n, int(inp.value)))
                                await db.commit()
                            await i3.response.send_message(f"✅ レシピを更新しました。", ephemeral=True)
                    await i2.response.send_modal(QtyModal(selected))
                s_mats.callback = s_mats_cb; v2.add_item(s_mats); await i.response.send_message(f"「{p_name}」の素材選択:", view=v2, ephemeral=True)
            s_rec.callback = rcb; v.add_item(s_rec)
        await interaction.response.send_message("登録・削除・レシピ設定:", view=v, ephemeral=True)

    @discord.ui.button(label="🏆 ランキング", style=discord.ButtonStyle.success, custom_id="adm_rank")
    async def view_rank(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
        txt = "🏆 **売上ランキング**\n" + "\n".join([f"{i+1}位: <@{r[0]}> - {r[1]}{CURRENCY}" for i, r in enumerate(rows)])
        v = discord.ui.View()
        if rows:
            s = discord.ui.Select(placeholder="個別にデータを削除"); [s.add_option(label=f"ID:{r[0]}", value=str(r[0])) for r in rows]
            async def cb(i):
                async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM sales_ranking WHERE user_id=?", (int(s.values[0]),)); await db.commit()
                await i.response.send_message("✅ 削除完了", ephemeral=True)
            s.callback = cb; v.add_item(s)
        await interaction.response.send_message(txt or "データなし", view=v, ephemeral=True)

    @discord.ui.button(label="⏰ 集計", style=discord.ButtonStyle.primary, custom_id="adm_sum")
    async def work_sum(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            bonus = int((await (await db.execute("SELECT value FROM config WHERE key='hourly_bonus'")).fetchone())[0])
            rows = await (await db.execute("SELECT user_id, SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE end IS NOT NULL GROUP BY user_id")).fetchall()
        txt = f"📊 **勤務集計 (時給:{bonus}{CURRENCY})**\n"
        v = discord.ui.View()
        if rows:
            s = discord.ui.Select(placeholder="個別に集計をリセット")
            for u_id, sec in rows:
                txt += f"👤 <@{u_id}>: {format_time(sec)}\n"
                s.add_option(label=f"ID:{u_id}", value=str(u_id))
            async def cb(i):
                async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM work_logs WHERE user_id=?", (int(s.values[0]),)); await db.commit()
                await i.response.send_message("✅ リセット完了", ephemeral=True)
            s.callback = cb; v.add_item(s)
        await interaction.response.send_message(txt, view=v, ephemeral=True)

    @discord.ui.button(label="💰 時給設定", style=discord.ButtonStyle.gray, custom_id="adm_bonus")
    async def bonus_set(self, interaction, button):
        class BModal(discord.ui.Modal, title="時給設定"):
            a = discord.ui.TextInput(label="1時間あたりの金額")
            async def on_submit(self, i):
                async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR REPLACE INTO config VALUES ('hourly_bonus', ?)", (self.a.value,)); await db.commit()
                await i.response.send_message(f"✅ 時給を {self.a.value}{CURRENCY} に設定しました。", ephemeral=True)
        await interaction.response.send_modal(BModal())

    @discord.ui.button(label="📜 履歴ログ", style=discord.ButtonStyle.gray, custom_id="adm_audit")
    async def view_audit(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, action, detail FROM audit_logs ORDER BY id DESC LIMIT 10")).fetchall()
        txt = "📜 **最新の操作履歴**\n```"
        for r in rows: txt += f"[{r[0][5:16]}] {r[1]}: {r[2]}\n"
        await interaction.response.send_message(txt + "```", ephemeral=True)

# ================= 5. 業務パネル =================

class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🟢 出勤", style=discord.ButtonStyle.success, custom_id="gen_in")
    async def in_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO work_logs VALUES (?,?,NULL)", (interaction.user.id, datetime.now())); await db.commit()
        await interaction.user.add_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message("🟢 出勤完了", ephemeral=True)

    @discord.ui.button(label="🔴 退勤", style=discord.ButtonStyle.danger, custom_id="gen_out")
    async def out_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            row = await (await db.execute("SELECT rowid FROM work_logs WHERE user_id=? AND end IS NULL", (interaction.user.id,))).fetchone()
            if not row: return await interaction.response.send_message("❌ 出勤記録なし", ephemeral=True)
            await db.execute("UPDATE work_logs SET end=? WHERE rowid=?", (datetime.now(), row[0])); await db.commit()
        await interaction.user.remove_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message("🔴 退勤完了", ephemeral=True)

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="gen_craft")
    async def craft_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name FROM products")).fetchall()
        if not prods: return await interaction.response.send_message("❌ 商品なし", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="制作物選択")
        for p in prods: s.add_option(label=p[0], value=p[0])
        async def cb(i):
            m = discord.ui.Modal(title="制作数"); q = discord.ui.TextInput(label="個数", default="1"); m.add_item(q)
            async def scb(mi):
                qty, pn = int(q.value), s.values[0]
                async with aiosqlite.connect(DB_PATH) as db:
                    recipe = await (await db.execute("SELECT material_name, quantity FROM recipes WHERE product_name=?", (pn,))).fetchall()
                    for mn, mq in recipe: await db.execute("UPDATE materials SET current = current - ? WHERE name=?", (mq*qty, mn))
                    await db.execute("UPDATE products SET current = current + ? WHERE name=?", (qty, pn))
                    await db.commit()
                await mi.response.send_message(f"✅ {pn} を {qty} 個制作しました", ephemeral=True)
            m.on_submit = scb; await i.response.send_modal(m)
        s.callback = cb; v.add_item(s); await interaction.response.send_message("制作報告:", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上登録", style=discord.ButtonStyle.secondary, custom_id="gen_sale")
    async def sale_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name, price FROM products")).fetchall()
        if not prods: return await interaction.response.send_message("❌ 商品なし", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="売れた商品を選択")
        for p in prods: s.add_option(label=f"{p[0]}({p[1]}{CURRENCY})", value=f"{p[0]}:{p[1]}")
        async def cb(i):
            pn, pr = s.values[0].split(":"); m = discord.ui.Modal(title="販売数"); q = discord.ui.TextInput(label="個数", default="1"); m.add_item(q)
            async def scb(mi):
                total = int(q.value) * int(pr)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE products SET current = current - ? WHERE name=?", (int(q.value), pn))
                    await db.execute("INSERT INTO sales_ranking (user_id, total_amount) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET total_amount = total_amount + ?", (mi.user.id, total, total))
                    await db.commit()
                await mi.response.send_message(f"💰 {total}{CURRENCY} 売上登録完了", ephemeral=True)
            m.on_submit = scb; await i.response.send_modal(m)
        s.callback = cb; v.add_item(s); await interaction.response.send_message("売上登録:", view=v, ephemeral=True)

# ================= 6. 起動 =================

@bot.event
async def on_ready():
    await init_db()
    bot.add_view(AdminPanel()); bot.add_view(GeneralPanel())
    print(f"Logged in: {bot.user}")
    for c_id, view, txt in [(ADMIN_PANEL_CHANNEL_ID, AdminPanel(), "🔧 **管理パネル**"), (GENERAL_PANEL_CHANNEL_ID, GeneralPanel(), "🧾 **業務パネル**")]:
        ch = bot.get_channel(c_id)
        if ch: await ch.purge(limit=5); await ch.send(txt, view=view)

bot.run(TOKEN)
