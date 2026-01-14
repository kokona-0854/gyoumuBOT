import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime
import os

# ================= 1. 各種設定 =================
TOKEN = "あなたのボットトークンをここに貼り付け"

# 各種ID（ご自身のサーバーのIDに書き換えてください）
ADMIN_ROLE_ID = 1459388566760325318      # 管理・商品管理パネルを操作できるロール
WORK_ROLE_ID = 1459209336076374068       # 勤務中ロール
ADMIN_PANEL_CH = 1459371812310745171     # 管理者パネル送信先
ITEM_PANEL_CH = 1459371812310745171      # 商品管理パネル送信先
GENERAL_PANEL_CH = 1458801073899966585   # 業務パネル送信先
ALERT_CH_ID = 1459371812310745171        # 在庫不足通知先

# メンバー管理ボタンで表示するロール設定 { "表示名": ロールID }
ROLE_OPTIONS = {
    "オムニス権限": 1459208662055911538,
    "管理者ロール": 1459388566760325318,
    "従業員ロール": 1455242976258297917,
}

DB_PATH = "master_system_v6.db"
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

async def check_alert(item_name, item_type):
    async with aiosqlite.connect(DB_PATH) as db:
        table = "materials" if item_type == "mat" else "products"
        row = await (await db.execute(f"SELECT current, threshold FROM {table} WHERE name=?", (item_name,))).fetchone()
        if row and row[1] > 0 and row[0] <= row[1]:
            ch = bot.get_channel(ALERT_CH_ID)
            if ch:
                embed = discord.Embed(title="⚠️ 在庫不足アラート", color=discord.Color.red(), timestamp=datetime.now())
                embed.add_field(name="アイテム名", value=item_name, inline=False)
                embed.add_field(name="現在庫", value=f"**{row[0]}**", inline=True)
                embed.add_field(name="通知しきい値", value=f"{row[1]}以下", inline=True)
                await ch.send(content="@here", embed=embed)

# 汎用入力モーダル
class GenericInputModal(discord.ui.Modal):
    def __init__(self, title, label, callback_func, placeholder=None, default=None):
        super().__init__(title=title)
        self.input_field = discord.ui.TextInput(label=label, placeholder=placeholder, default=default)
        self.add_item(self.input_field)
        self.callback_func = callback_func
    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, self.input_field.value)

# ================= 3. View 定義 =================

# --- 商品管理パネル ---
class ItemPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator or any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles):
            return True
        await interaction.response.send_message("❌ このパネルを操作する権限がありません。", ephemeral=True)
        return False

    @discord.ui.button(label="📜 登録・レシピ・削除・価格変更", style=discord.ButtonStyle.primary, custom_id="item_v6_reg")
    async def reg_menu(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            p = await (await db.execute("SELECT name, price FROM products")).fetchall()
            m = await (await db.execute("SELECT name FROM materials")).fetchall()
        
        view = discord.ui.View()
        
        # --- 登録・価格変更 ---
        async def add_p_cb(i, val):
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR IGNORE INTO products (name) VALUES (?)", (val,))
                await db.commit()
            await i.response.send_message(f"✅ 商品 {val} を登録しました。", ephemeral=True)
        
        async def add_m_cb(i, val):
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR IGNORE INTO materials (name) VALUES (?)", (val,))
                await db.commit()
            await i.response.send_message(f"✅ 素材 {val} を登録しました。", ephemeral=True)

        btn_p = discord.ui.Button(label="商品追加", style=discord.ButtonStyle.success)
        btn_p.callback = lambda x: x.response.send_modal(GenericInputModal("商品登録", "商品名を入力", add_p_cb))
        btn_m = discord.ui.Button(label="素材追加", style=discord.ButtonStyle.success)
        btn_m.callback = lambda x: x.response.send_modal(GenericInputModal("素材登録", "素材名を入力", add_m_cb))
        view.add_item(btn_p).add_item(btn_m)

        # --- レシピ設定 ---
        if p and m:
            sel_r = discord.ui.Select(placeholder="レシピ設定(商品選択)", row=1)
            for x in p: sel_r.add_option(label=x[0], value=x[0])
            async def r_cb(i2):
                v2 = discord.ui.View(); sel_m = discord.ui.Select(placeholder="使用する素材を選択")
                for x in m: sel_m.add_option(label=x[0], value=x[0])
                async def qty_cb(i3, val):
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (sel_r.values[0], sel_m.values[0], int(val)))
                        await db.commit()
                    await i3.response.send_message(f"✅ 【{sel_r.values[0]}】に【{sel_m.values[0]}】を{val}個設定しました。", ephemeral=True)
                sel_m.callback = lambda i3: i3.response.send_modal(GenericInputModal("個数設定", "必要個数を入力", qty_cb, default="1"))
                v2.add_item(sel_m); await i2.response.send_message(f"【{sel_r.values[0]}】の素材選択:", view=v2, ephemeral=True)
            sel_r.callback = r_cb; view.add_item(sel_r)

        # --- 各種管理メニュー ---
        if p or m:
            sel_mng = discord.ui.Select(placeholder="🛠️ 個別操作(価格・削除・アラート)", row=2)
            for x in p: sel_mng.add_option(label=f"商品: {x[0]} (現在{x[1]}円)", value=f"p:{x[0]}")
            for x in m: sel_mng.add_option(label=f"素材: {x[0]}", value=f"m:{x[0]}")
            async def mng_cb(i2):
                mode, name = sel_mng.values[0].split(":")
                v3 = discord.ui.View()
                
                # 削除
                async def del_item(i3):
                    async with aiosqlite.connect(DB_PATH) as db:
                        if mode == "p": await db.execute("DELETE FROM products WHERE name=?", (name,)); await db.execute("DELETE FROM recipes WHERE product_name=?", (name,))
                        else: await db.execute("DELETE FROM materials WHERE name=?", (name,)); await db.execute("DELETE FROM recipes WHERE material_name=?", (name,))
                        await db.commit()
                    await i3.response.send_message(f"🗑️ {name} を完全に削除しました。", ephemeral=True)
                
                # 単価変更（商品のみ）
                async def price_act(i3, val):
                    async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE products SET price=? WHERE name=?", (int(val), name)); await db.commit()
                    await i3.response.send_message(f"✅ {name} の単価を {val}{CURRENCY} に変更しました。", ephemeral=True)
                
                # アラート設定
                async def alert_act(i3, val):
                    tbl = "products" if mode == "p" else "materials"
                    async with aiosqlite.connect(DB_PATH) as db: await db.execute(f"UPDATE {tbl} SET threshold=? WHERE name=?", (int(val), name)); await db.commit()
                    await i3.response.send_message(f"✅ {name} のアラートを {val}個以下 に設定しました。", ephemeral=True)

                b_del = discord.ui.Button(label="削除", style=discord.ButtonStyle.danger)
                b_del.callback = del_item; v3.add_item(b_del)
                
                if mode == "p":
                    b_prc = discord.ui.Button(label="単価変更", style=discord.ButtonStyle.primary)
                    b_prc.callback = lambda x: x.response.send_modal(GenericInputModal("単価変更", "新しい価格を入力", price_act))
                    v3.add_item(b_prc)

                b_alt = discord.ui.Button(label="通知設定", style=discord.ButtonStyle.secondary)
                b_alt.callback = lambda x: x.response.send_modal(GenericInputModal("アラート設定", "通知する個数を入力", alert_act, default="5"))
                v3.add_item(b_alt)
                
                await i2.response.send_message(f"【{name}】に対する操作を選んでください:", view=v3, ephemeral=True)
            sel_mng.callback = mng_cb; view.add_item(sel_mng)

        await interaction.response.send_message("商品・レシピ・管理メニュー:", view=view, ephemeral=True)

    @discord.ui.button(label="📦 在庫・素材補充/引出", style=discord.ButtonStyle.secondary, custom_id="item_v6_stock")
    async def stock_menu(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            m = await (await db.execute("SELECT name, current, threshold FROM materials")).fetchall()
            p = await (await db.execute("SELECT name, current, threshold FROM products")).fetchall()
        txt = "📦 **現在庫一覧**\n\n**素材:** " + (", ".join([f"{x[0]}:`{x[1]}`(🔔{x[2]})" for x in m]) if m else "なし")
        txt += "\n**商品:** " + (", ".join([f"{x[0]}:`{x[1]}`(🔔{x[2]})" for x in p]) if p else "なし")
        view = discord.ui.View()
        if m:
            sel = discord.ui.Select(placeholder="在庫調整する素材を選択")
            for x in m: sel.add_option(label=x[0], value=x[0])
            async def adj_cb(i2, val):
                v = int(val)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE materials SET current = current + ? WHERE name=?", (v, sel.values[0]))
                    await db.commit()
                await check_alert(sel.values[0], "mat")
                act = "補充" if v > 0 else "引出"
                await add_audit(i2.user.id, f"素材{act}", f"{sel.values[0]} ({v:+})")
                await i2.response.send_message(f"✅ {sel.values[0]} を {v} 調整しました。", ephemeral=True)
            sel.callback = lambda i2: i2.response.send_modal(GenericInputModal("在庫調整", "数量を入力 (+補充 / -引出)", adj_cb))
            view.add_item(sel)
        await interaction.response.send_message(txt, view=view, ephemeral=True)

# --- 管理者パネル (AdminPanel) ---
class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator or any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles):
            return True
        await interaction.response.send_message("❌ 管理権限がありません。", ephemeral=True)
        return False

    @discord.ui.button(label="👤 メンバー管理", style=discord.ButtonStyle.success, custom_id="adm_v6_mem")
    async def member(self, interaction, button):
        view = discord.ui.View(); sel = discord.ui.Select(placeholder="ロールを選択")
        for n, rid in ROLE_OPTIONS.items(): sel.add_option(label=n, value=str(rid))
        async def m_cb(i2):
            rid = int(sel.values[0]); v2 = discord.ui.View()
            async def role_act(i3, uid, action):
                try:
                    target = i3.guild.get_member(int(uid)); role = i3.guild.get_role(rid)
                    if action == "add": await target.add_roles(role)
                    else: await target.remove_roles(role)
                    await i3.response.send_message("✅ ロールを更新しました。", ephemeral=True)
                except: await i3.response.send_message("❌ IDが不正か、ボットがそのユーザーを見つけられません。", ephemeral=True)
            b1 = discord.ui.Button(label="付与", style=discord.ButtonStyle.primary)
            b1.callback = lambda x: x.response.send_modal(GenericInputModal("付与", "ユーザーIDを入力", lambda i4, v: role_act(i4, v, "add")))
            b2 = discord.ui.Button(label="剥奪", style=discord.ButtonStyle.danger)
            b2.callback = lambda x: x.response.send_modal(GenericInputModal("剥奪", "ユーザーIDを入力", lambda i4, v: role_act(i4, v, "rem")))
            v2.add_item(b1).add_item(b2); await i2.response.send_message("操作を選択:", view=v2, ephemeral=True)
        sel.callback = m_cb; view.add_item(sel); await interaction.response.send_message("メンバー管理:", view=view, ephemeral=True)

    @discord.ui.button(label="🏆 統計/勤務集計", style=discord.ButtonStyle.gray, custom_id="adm_v6_stat")
    async def stats(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rank = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
            work = await (await db.execute("SELECT user_id, SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE end IS NOT NULL GROUP BY user_id")).fetchall()
        txt = "🏆 **売上ランキング**\n" + "\n".join([f"{idx+1}. <@{r[0]}>: {r[1]:,}{CURRENCY}" for idx, r in enumerate(rank)]) if rank else "売上データなし"
        txt += "\n\n📊 **累計勤務時間**\n" + "\n".join([f"・<@{w[0]}>: `{int(w[1]//60)}分`" for w in work]) if work else "勤務データなし"
        await interaction.response.send_message(txt, ephemeral=True)

    @discord.ui.button(label="📜 履歴ログ", style=discord.ButtonStyle.gray, custom_id="adm_v6_log")
    async def logs(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, user_id, action, detail FROM audit_logs ORDER BY id DESC LIMIT 15")).fetchall()
        txt = "📜 **履歴 (最新15件)**\n" + "\n".join([f"`{r[0][5:16]}` <@{r[1]}> **{r[2]}**: {r[3]}" for r in rows]) if rows else "ログなし"
        await interaction.response.send_message(txt, ephemeral=True)

# --- 業務パネル (GeneralPanel) ---
class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🟢 出勤", style=discord.ButtonStyle.success, custom_id="gen_v6_in")
    async def cin(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT INTO work_logs VALUES (?,?,NULL)", (i.user.id, datetime.now())); await db.commit()
        await i.user.add_roles(i.guild.get_role(WORK_ROLE_ID))
        await i.response.send_message("🟢 出勤完了しました。", ephemeral=True)

    @discord.ui.button(label="🔴 退勤", style=discord.ButtonStyle.danger, custom_id="gen_v6_out")
    async def cout(self, i, b):
        now = datetime.now()
        async with aiosqlite.connect(DB_PATH) as db:
            row = await (await db.execute("SELECT start FROM work_logs WHERE user_id=? AND end IS NULL ORDER BY start DESC LIMIT 1", (i.user.id,))).fetchone()
            if not row: return await i.response.send_message("❌ 出勤記録がありません。", ephemeral=True)
            this_m = int((now - datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S.%f")).total_seconds() // 60)
            await db.execute("UPDATE work_logs SET end=? WHERE user_id=? AND end IS NULL", (now, i.user.id))
            await db.commit()
        await i.user.remove_roles(i.guild.get_role(WORK_ROLE_ID))
        await i.response.send_message(f"🔴 退勤完了: 今回 `{this_m}分` 勤務しました。", ephemeral=True)

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="gen_v6_craft")
    async def craft(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name, current FROM products")).fetchall()
        if not prods: return await i.response.send_message("❌ 商品が登録されていません。", ephemeral=True)
        v = discord.ui.View(); sel = discord.ui.Select(placeholder="制作した商品を選択")
        for x in prods: sel.add_option(label=f"{x[0]} (在庫:{x[1]})", value=x[0])
        async def c_cb(i2, val):
            qty = int(val)
            async with aiosqlite.connect(DB_PATH) as db:
                recs = await (await db.execute("SELECT material_name, quantity FROM recipes WHERE product_name=?", (sel.values[0],))).fetchall()
                for mn, mq in recs:
                    stk = await (await db.execute("SELECT current FROM materials WHERE name=?", (mn,))).fetchone()
                    if not stk or stk[0] < (mq * qty): return await i2.response.send_message(f"❌ 素材不足: {mn}", ephemeral=True)
                for mn, mq in recs: await db.execute("UPDATE materials SET current = current - ? WHERE name=?", (mq*qty, mn))
                await db.execute("UPDATE products SET current = current + ? WHERE name=?", (qty, sel.values[0]))
                await db.commit()
            for mn, _ in recs: await check_alert(mn, "mat")
            await add_audit(i2.user.id, "制作", f"{sel.values[0]} x{qty}")
            await i2.response.send_message(f"✅ 制作完了: {sel.values[0]} x{qty}", ephemeral=True)
        sel.callback = lambda i2: i2.response.send_modal(GenericInputModal("制作", "制作数を入力", c_cb, default="1"))
        v.add_item(sel); await i.response.send_message("制作報告:", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上登録", style=discord.ButtonStyle.success, custom_id="gen_v6_sale")
    async def sale(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db: prods = await (await db.execute("SELECT name, price, current FROM products")).fetchall()
        if not prods: return await i.response.send_message("❌ 商品が登録されていません。", ephemeral=True)
        v = discord.ui.View(); sel = discord.ui.Select(placeholder="販売した商品を選択")
        for x in prods: sel.add_option(label=f"{x[0]} ({x[1]}円 / 在庫:{x[2]})", value=f"{x[0]}:{x[1]}")
        async def s_cb(i2, val):
            name, price = sel.values[0].split(":"); qty = int(val); total = qty * int(price)
            async with aiosqlite.connect(DB_PATH) as db:
                stk = await (await db.execute("SELECT current FROM products WHERE name=?", (name,))).fetchone()
                if not stk or stk[0] < qty: return await i2.response.send_message("❌ 在庫不足です。", ephemeral=True)
                await db.execute("UPDATE products SET current = current - ? WHERE name=?", (qty, name))
                await db.execute("INSERT INTO sales_ranking (user_id, total_amount) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET total_amount = total_amount + ?", (i2.user.id, total, total))
                await db.commit()
            await check_alert(name, "prod")
            await add_audit(i2.user.id, "売上", f"{name} x{qty} ({total:,}円)")
            await i2.response.send_message(f"💰 売上登録完了: {name} x{qty}", ephemeral=True)
        sel.callback = lambda i2: i2.response.send_modal(GenericInputModal("売上", "販売数を入力", s_cb, default="1"))
        v.add_item(sel); await i.response.send_message("売上登録:", view=v, ephemeral=True)

# ================= 4. 起動処理 =================
@bot.event
async def on_ready():
    await init_db()
    bot.add_view(AdminPanel()); bot.add_view(ItemPanel()); bot.add_view(GeneralPanel())
    print(f"Logged in as {bot.user}")

    for ch_id, view, text in [(ADMIN_PANEL_CH, AdminPanel(), "🔧 **管理者パネル**"), (ITEM_PANEL_CH, ItemPanel(), "📦 **商品管理パネル**"), (GENERAL_PANEL_CH, GeneralPanel(), "🧾 **業務パネル**")]:
        ch = bot.get_channel(ch_id)
        if ch:
            await ch.purge(limit=10)
            await ch.send(text, view=view)

bot.run(TOKEN)
