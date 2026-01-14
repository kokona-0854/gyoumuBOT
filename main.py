import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime
import os
import traceback
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
        CREATE TABLE IF NOT EXISTS materials(name TEXT PRIMARY KEY, current INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS products(name TEXT PRIMARY KEY, price INTEGER DEFAULT 0, current INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS recipes(product_name TEXT, material_name TEXT, quantity INTEGER, PRIMARY KEY(product_name, material_name));
        CREATE TABLE IF NOT EXISTS sales_ranking(user_id INTEGER PRIMARY KEY, total_amount INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, detail TEXT, created_at DATETIME);
        """)
        await db.commit()

async def add_audit(user_id, action, detail):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO audit_logs (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
                        (user_id, action, detail, datetime.now()))
        await db.commit()

# ================= 3. モーダル定義 =================

class RoleInputModal(discord.ui.Modal, title="ユーザーID入力"):
    uid = discord.ui.TextInput(label="対象者のユーザーID", placeholder="数字のみ入力")
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

class StockAdjustModal(discord.ui.Modal):
    def __init__(self, item_name):
        super().__init__(title=f"素材「{item_name}」の補充/引出")
        self.item_name = item_name
        self.qty = discord.ui.TextInput(label="調整数", placeholder="例: 10 (補充) または -5 (引出)")
        self.add_item(self.qty)
    async def on_submit(self, i: discord.Interaction):
        try:
            val = int(self.qty.value)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE materials SET current = current + ? WHERE name = ?", (val, self.item_name))
                await db.commit()
            await add_audit(i.user.id, "MAT_ADJ", f"{self.item_name} ({val})")
            await i.response.send_message(f"✅ {self.item_name} を {val} 調整しました。", ephemeral=True)
        except: await i.response.send_message("❌ 数値を入力してください。", ephemeral=True)

class ItemAddModal(discord.ui.Modal):
    def __init__(self, mode):
        super().__init__(title="商品登録" if mode == "prod" else "素材登録")
        self.mode, self.name_in = mode, discord.ui.TextInput(label="名前")
        self.add_item(self.name_in)
        if mode == "prod": self.price_in = discord.ui.TextInput(label="単価", default="1000"); self.add_item(self.price_in)
    async def on_submit(self, i: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            if self.mode == "prod": 
                await db.execute("INSERT OR REPLACE INTO products (name, price, current) VALUES (?,?, COALESCE((SELECT current FROM products WHERE name=?), 0))", (self.name_in.value, int(self.price_in.value), self.name_in.value))
            else: 
                await db.execute("INSERT OR REPLACE INTO materials (name, current) VALUES (?, COALESCE((SELECT current FROM materials WHERE name=?), 0))", (self.name_in.value, self.name_in.value))
            await db.commit()
        await i.response.send_message(f"✅ {self.name_in.value} を登録しました。", ephemeral=True)

class RecipeQuantityModal(discord.ui.Modal, title="必要個数の入力"):
    def __init__(self, p_name, m_list):
        super().__init__(); self.p_name, self.m_list, self.inputs = p_name, m_list, []
        for m_name in m_list[:5]:
            t = discord.ui.TextInput(label=f"{m_name} の必要数", default="1")
            self.add_item(t); self.inputs.append((m_name, t))
    async def on_submit(self, i: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            for m_name, t_input in self.inputs:
                await db.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (self.p_name, m_name, int(t_input.value)))
            await db.commit()
        await i.response.send_message(f"✅ {self.p_name} のレシピを設定しました。", ephemeral=True)

class IndividualResetModal(discord.ui.Modal):
    def __init__(self, target_table):
        super().__init__(title="個人データリセット")
        self.target_table = target_table
        self.uid_input = discord.ui.TextInput(label="対象者のユーザーID")
        self.add_item(self.uid_input)
    async def on_submit(self, i: discord.Interaction):
        uid = int(self.uid_input.value)
        async with aiosqlite.connect(DB_PATH) as db:
            if self.target_table == "ranking": await db.execute("DELETE FROM sales_ranking WHERE user_id = ?", (uid,))
            else: await db.execute("DELETE FROM work_logs WHERE user_id = ?", (uid,))
            await db.commit()
        await i.response.send_message(f"✅ ID:{uid} のデータをリセットしました。", ephemeral=True)

# ================= 4. 管理パネル =================

class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="👤 メンバー管理", style=discord.ButtonStyle.success, custom_id="adm_mem_v_final")
    async def member_mgmt(self, interaction, button):
        v = discord.ui.View(); s = discord.ui.Select(placeholder="ロールを選択")
        for n, rid in ROLE_OPTIONS.items(): s.add_option(label=n, value=str(rid))
        async def scb(i):
            rid = int(s.values[0]); v2 = discord.ui.View()
            b1 = discord.ui.Button(label="付与", style=discord.ButtonStyle.success); b1.callback = lambda i2: i2.response.send_modal(RoleInputModal(rid, "add"))
            b2 = discord.ui.Button(label="削除", style=discord.ButtonStyle.danger); b2.callback = lambda i2: i2.response.send_modal(RoleInputModal(rid, "rem"))
            v2.add_item(b1).add_item(b2); await i.response.send_message("操作:", view=v2, ephemeral=True)
        s.callback = scb; v.add_item(s); await interaction.response.send_message("メンバー管理:", view=v, ephemeral=True)

    @discord.ui.button(label="📦 在庫・素材補充", style=discord.ButtonStyle.secondary, custom_id="adm_stk_v_final")
    async def stock_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            m = await (await db.execute("SELECT name, current FROM materials")).fetchall()
            p = await (await db.execute("SELECT name, current FROM products")).fetchall()
        txt = "📦 **現在庫一覧**\n\n**【素材】**\n" + ("\n".join([f"・{x[0]}: `{x[1]}`" for x in m]) if m else "なし")
        txt += "\n\n**【商品】**\n" + ("\n".join([f"・{x[0]}: `{x[1]}`" for x in p]) if p else "なし")
        if not m: return await interaction.response.send_message(txt + "\n\n※補充できる素材がありません。", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="補充・引出する【素材】を選択")
        for x in m: s.add_option(label=f"{x[0]} (現在: {x[1]})", value=x[0])
        s.callback = lambda i: i.response.send_modal(StockAdjustModal(s.values[0]))
        v.add_item(s); await interaction.response.send_message(txt, view=v, ephemeral=True)

    @discord.ui.button(label="📜 登録・レシピ・削除", style=discord.ButtonStyle.primary, custom_id="adm_reg_v_final")
    async def reg_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name FROM products")).fetchall()
            mats = await (await db.execute("SELECT name FROM materials")).fetchall()
        v = discord.ui.View()
        v.add_item(discord.ui.Button(label="商品追加", style=discord.ButtonStyle.success, custom_id="btn_ap")).callback = lambda i: i.response.send_modal(ItemAddModal("prod"))
        v.add_item(discord.ui.Button(label="素材追加", style=discord.ButtonStyle.success, custom_id="btn_am")).callback = lambda i: i.response.send_modal(ItemAddModal("mat"))
        if prods and mats:
            s_r = discord.ui.Select(placeholder="レシピ設定(商品選択)", row=1)
            for p in prods: s_r.add_option(label=p[0], value=p[0])
            async def rcb(i):
                v2 = discord.ui.View(); ms = discord.ui.Select(placeholder="素材を選択(5つまで)", min_values=1, max_values=min(len(mats), 5))
                for m in mats: ms.add_option(label=m[0], value=m[0])
                ms.callback = lambda i2: i2.response.send_modal(RecipeQuantityModal(s_r.values[0], ms.values))
                v2.add_item(ms); await i.response.send_message("素材選択:", view=v2, ephemeral=True)
            s_r.callback = rcb; v.add_item(s_r)
        if prods or mats:
            s_d = discord.ui.Select(placeholder="🗑️ 登録削除メニュー", row=2)
            for p in prods: s_d.add_option(label=f"商品削除: {p[0]}", value=f"del_p:{p[0]}")
            for m in mats: s_d.add_option(label=f"素材削除: {m[0]}", value=f"del_m:{m[0]}")
            async def dcb(i):
                cmd, name = s_d.values[0].split(":")
                async with aiosqlite.connect(DB_PATH) as db:
                    if cmd == "del_p": await db.execute("DELETE FROM products WHERE name=?", (name,)); await db.execute("DELETE FROM recipes WHERE product_name=?", (name,))
                    else: await db.execute("DELETE FROM materials WHERE name=?", (name,)); await db.execute("DELETE FROM recipes WHERE material_name=?", (name,))
                    await db.commit()
                await i.response.send_message(f"🗑️ {name} を削除しました。", ephemeral=True)
            s_d.callback = dcb; v.add_item(s_d)
        await interaction.response.send_message("登録・レシピ・削除:", view=v, ephemeral=True)

    @discord.ui.button(label="🏆 ランキング管理", style=discord.ButtonStyle.success, custom_id="adm_rank_v_final")
    async def rank_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
        txt = "🏆 **売上ランキング**\n" + "\n".join([f"{i+1}位: <@{r[0]}> {r[1]:,}{CURRENCY}" for i, r in enumerate(rows)]) if rows else "データなし"
        v = discord.ui.View(); b_all = discord.ui.Button(label="全体リセット", style=discord.ButtonStyle.danger)
        async def ra(i): async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM sales_ranking"); await db.commit(); await i.response.send_message("リセット完了", ephemeral=True)
        b_all.callback = ra; b_ind = discord.ui.Button(label="個人リセット", style=discord.ButtonStyle.secondary); b_ind.callback = lambda i: i.response.send_modal(IndividualResetModal("ranking"))
        v.add_item(b_all).add_item(b_ind); await interaction.response.send_message(txt, view=v, ephemeral=True)

    @discord.ui.button(label="⏰ 勤務集計管理", style=discord.ButtonStyle.gray, custom_id="adm_work_v_final")
    async def work_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT user_id, SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE end IS NOT NULL GROUP BY user_id")).fetchall()
        txt = "📊 **勤務集計（分単位）**\n" + "\n".join([f"・<@{r[0]}>: `{int(r[1]//60)}分`" for r in rows]) if rows else "データなし"
        v = discord.ui.View(); b_all = discord.ui.Button(label="全ログ削除", style=discord.ButtonStyle.danger)
        async def wa(i): async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM work_logs"); await db.commit(); await i.response.send_message("全消去完了", ephemeral=True)
        b_all.callback = wa; b_ind = discord.ui.Button(label="個人リセット", style=discord.ButtonStyle.secondary); b_ind.callback = lambda i: i.response.send_modal(IndividualResetModal("work"))
        v.add_item(b_all).add_item(b_ind); await interaction.response.send_message(txt, view=v, ephemeral=True)

    @discord.ui.button(label="📜 履歴ログ", style=discord.ButtonStyle.gray, custom_id="adm_audit_v_final")
    async def audit_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, user_id, action, detail FROM audit_logs ORDER BY id DESC LIMIT 15")).fetchall()
        if not rows: return await interaction.response.send_message("履歴はありません。", ephemeral=True)
        txt = "📜 **最新操作履歴 (15件)**\n" + "\n".join([f"`{r[0][5:16]}` <@{r[1]}> **{r[2]}**: {r[3]}" for r in rows])
        await interaction.response.send_message(txt, ephemeral=True)

# ================= 5. 業務パネル =================

class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🟢 出勤", style=discord.ButtonStyle.success, custom_id="gen_in_v_final")
    async def in_btn(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT INTO work_logs VALUES (?,?,NULL)", (i.user.id, datetime.now())); await db.commit()
        await i.user.add_roles(i.guild.get_role(WORK_ROLE_ID)); await i.response.send_message("🟢 出勤しました。", ephemeral=True)

    @discord.ui.button(label="🔴 退勤", style=discord.ButtonStyle.danger, custom_id="gen_out_v_final")
    async def out_btn(self, i, b):
        now = datetime.now()
        async with aiosqlite.connect(DB_PATH) as db:
            row = await (await db.execute("SELECT start FROM work_logs WHERE user_id=? AND end IS NULL ORDER BY start DESC LIMIT 1", (i.user.id,))).fetchone()
            if not row: return await i.response.send_message("❌ 出勤記録がありません。", ephemeral=True)
            this_min = int((now - datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S.%f")).total_seconds() // 60)
            await db.execute("UPDATE work_logs SET end=? WHERE user_id=? AND end IS NULL", (now, i.user.id))
            total = await (await db.execute("SELECT SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE user_id=? AND end IS NOT NULL", (i.user.id,))).fetchone()
            total_min = int(total[0] // 60) if total[0] else this_min
            await db.commit()
        await i.user.remove_roles(i.guild.get_role(WORK_ROLE_ID))
        await i.response.send_message(f"🔴 退勤完了\n今回の勤務: `{this_min}分` / 累計: `{total_min}分`", ephemeral=True)

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="gen_craft_v_final")
    async def craft_btn(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name, current FROM products")).fetchall()
        if not prods: return await i.response.send_message("❌ 商品未登録", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="制作物選択")
        for p in prods: s.add_option(label=f"{p[0]} (在庫: {p[1]})", value=p[0])
        async def scb(i2):
            class CModal(discord.ui.Modal, title=f"「{s.values[0]}」制作"):
                q = discord.ui.TextInput(label="制作個数", default="1")
                async def on_submit(self, i3):
                    qty = int(self.q.value)
                    async with aiosqlite.connect(DB_PATH) as db:
                        recs = await (await db.execute("SELECT material_name, quantity FROM recipes WHERE product_name=?", (s.values[0],))).fetchall()
                        for mn, mq in recs:
                            mat = await (await db.execute("SELECT current FROM materials WHERE name=?", (mn,))).fetchone()
                            if not mat or mat[0] < (mq * qty): return await i3.response.send_message(f"❌ 素材不足: {mn}", ephemeral=True)
                        for mn, mq in recs: await db.execute("UPDATE materials SET current = current - ? WHERE name=?", (mq * qty, mn))
                        await db.execute("UPDATE products SET current = current + ? WHERE name=?", (qty, s.values[0]))
                        new = await (await db.execute("SELECT current FROM products WHERE name=?", (s.values[0],))).fetchone()
                        await db.commit()
                    await add_audit(i3.user.id, "制作", f"{s.values[0]} x{qty} (在庫:{new[0]})")
                    await i3.response.send_message(f"✅ {s.values[0]}制作 (+{qty}) / 現在庫: `{new[0]}`", ephemeral=True)
            await i2.response.send_modal(CModal())
        s.callback = scb; v.add_item(s); await i.response.send_message("制作報告:", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上登録", style=discord.ButtonStyle.success, custom_id="gen_sale_v_final")
    async def sale_btn(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name, price, current FROM products")).fetchall()
        if not prods: return await i.response.send_message("❌ 商品未登録", ephemeral=True)
        v = discord.ui.View(); s = discord.ui.Select(placeholder="販売物選択")
        for p in prods: s.add_option(label=f"{p[0]} (単価:{p[1]} / 在庫:{p[2]})", value=f"{p[0]}:{p[1]}")
        async def scb(i2):
            pn, pp = s.values[0].split(":")
            class SModal(discord.ui.Modal, title=f"「{pn}」販売"):
                q = discord.ui.TextInput(label="販売個数", default="1")
                async def on_submit(self, i3):
                    qty = int(self.q.value); amt = qty * int(pp)
                    async with aiosqlite.connect(DB_PATH) as db:
                        stk = await (await db.execute("SELECT current FROM products WHERE name=?", (pn,))).fetchone()
                        if not stk or stk[0] < qty: return await i3.response.send_message("❌ 在庫不足", ephemeral=True)
                        await db.execute("UPDATE products SET current = current - ? WHERE name=?", (qty, pn))
                        await db.execute("INSERT INTO sales_ranking (user_id, total_amount) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET total_amount = total_amount + ?", (i3.user.id, amt, amt))
                        new = await (await db.execute("SELECT current FROM products WHERE name=?", (pn, ))).fetchone()
                        await db.commit()
                    await add_audit(i3.user.id, "売上", f"{pn} x{qty} (在庫:{new[0]})")
                    await i3.response.send_message(f"💰 {pn}販売 (-{qty}) / 金額: `{amt:,}{CURRENCY}` / 現在庫: `{new[0]}`", ephemeral=True)
            await i2.response.send_modal(SModal())
        s.callback = scb; v.add_item(s); await i.response.send_message("売上登録:", view=v, ephemeral=True)

# ================= 6. 起動処理 =================
@bot.event
async def on_ready():
    await init_db()
    bot.add_view(AdminPanel()); bot.add_view(GeneralPanel())
    print(f"✅ システムオンライン: {bot.user}")
    for c_id, view, txt in [(ADMIN_PANEL_CHANNEL_ID, AdminPanel(), "🔧 **管理パネル**"), (GENERAL_PANEL_CHANNEL_ID, GeneralPanel(), "🧾 **業務パネル**")]:
        ch = bot.get_channel(c_id)
        if ch: 
            await ch.purge(limit=5)
            await ch.send(txt, view=view)

bot.run(TOKEN)
