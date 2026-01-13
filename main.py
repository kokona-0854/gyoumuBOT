import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime
import os
import json
from dotenv import load_dotenv

# ================= 1. 設定 =================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

OMNIS_ROLE_ID = 1459208662055911538
WORK_ROLE_ID = 1459209336076374068
ADMIN_ROLE_ID = 1459388566760325318

# 専用チャンネルID
ADMIN_PANEL_CHANNEL_ID = 1459371812310745171
GENERAL_PANEL_CHANNEL_ID = 1458801073899966585

DB_PATH = "data.db"
CURRENCY = "円"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= 2. DB・共通関数 =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(f"""
        CREATE TABLE IF NOT EXISTS work_logs(user_id INTEGER, start DATETIME, end DATETIME);
        CREATE TABLE IF NOT EXISTS materials(name TEXT PRIMARY KEY, current INTEGER, threshold INTEGER);
        CREATE TABLE IF NOT EXISTS products(name TEXT PRIMARY KEY, price INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, items TEXT, total_price INTEGER, created_at DATETIME);
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

def is_admin(member: discord.Member):
    return any(role.id == ADMIN_ROLE_ID for role in member.roles)

# ================= 3. モーダル類 =================

class RoleManageModal(discord.ui.Modal):
    def __init__(self, mode):
        super().__init__(title="ロール管理")
        self.mode = mode
        self.uid_input = discord.ui.TextInput(label="ユーザーID", placeholder="数字のみ入力")
        self.add_item(self.uid_input)
    async def on_submit(self, interaction):
        try:
            uid = int(self.uid_input.value)
            member = interaction.guild.get_member(uid)
            if not member: return await interaction.response.send_message("❌ メンバーが見つかりません。", ephemeral=True)
            role = interaction.guild.get_role(OMNIS_ROLE_ID)
            if self.mode == "add":
                await member.add_roles(role)
                act = "ROLE_ADD"
            else:
                await member.remove_roles(role)
                act = "ROLE_DEL"
            await add_audit(interaction.user.id, act, f"{member.display_name}")
            await interaction.response.send_message(f"✅ {member.display_name} の権限を更新しました。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ エラー: {e}", ephemeral=True)

class BonusSetModal(discord.ui.Modal, title="ボーナス設定"):
    amt = discord.ui.TextInput(label="1時間あたりの支給額", placeholder="例: 5000")
    async def on_submit(self, interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('hourly_bonus', ?)", (self.amt.value,))
            await db.commit()
        await add_audit(interaction.user.id, "SET_BONUS", f"{self.amt.value}{CURRENCY}")
        await interaction.response.send_message(f"✅ 時給ボーナスを {self.amt.value}{CURRENCY} に設定しました。", ephemeral=True)

class MaterialAddModal(discord.ui.Modal, title="素材登録"):
    name = discord.ui.TextInput(label="素材名")
    threshold = discord.ui.TextInput(label="目標在庫", default="10")
    async def on_submit(self, interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO materials VALUES (?, 0, ?)", (self.name.value, int(self.threshold.value)))
            await db.commit()
        await add_audit(interaction.user.id, "MAT_REG", self.name.value)
        await interaction.response.send_message(f"✅ 素材「{self.name.value}」を登録しました。", ephemeral=True)

class StockAdjustModal(discord.ui.Modal):
    def __init__(self, mat_name, mode):
        super().__init__(title=f"{mat_name} の数量変更")
        self.mat_name, self.mode = mat_name, mode
        self.amt = discord.ui.TextInput(label="変動させる個数")
        self.add_item(self.amt)
    async def on_submit(self, interaction):
        val = int(self.amt.value)
        change = val if self.mode == "plus" else -val
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE materials SET current = current + ? WHERE name = ?", (change, self.mat_name))
            await db.commit()
        await add_audit(interaction.user.id, "STOCK_ADJ", f"{self.mat_name} x{change}")
        await interaction.response.send_message(f"✅ 在庫を更新しました。", ephemeral=True)

class SaleFinalizeModal(discord.ui.Modal):
    def __init__(self, name, price):
        super().__init__(title=f"{name} の売上登録")
        self.name, self.price = name, price
        self.qty = discord.ui.TextInput(label="販売個数", default="1")
        self.add_item(self.qty)
    async def on_submit(self, interaction):
        total = int(self.qty.value) * self.price
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO sales (user_id, items, total_price, created_at) VALUES (?,?,?,?)",
                            (interaction.user.id, json.dumps({self.name: int(self.qty.value)}), total, datetime.now()))
            await db.commit()
        await add_audit(interaction.user.id, "SALE", f"{self.name} x{self.qty.value}")
        await interaction.response.send_message(f"💰 売上 {total}{CURRENCY} を登録しました。", ephemeral=True)

class ProductDefineModal(discord.ui.Modal, title="商品登録"):
    name = discord.ui.TextInput(label="商品名")
    price = discord.ui.TextInput(label="販売価格")
    async def on_submit(self, interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO products (name, price) VALUES (?,?)", (self.name.value, int(self.price.value)))
            await db.commit()
        await add_audit(interaction.user.id, "PROD_REG", self.name.value)
        await interaction.response.send_message(f"✅ 商品「{self.name.value}」を登録しました。", ephemeral=True)

# ================= 4. パネル View =================

class CraftProcessView(discord.ui.View):
    def __init__(self, p_name, p_qty, mats_list):
        super().__init__(timeout=None)
        self.p_name, self.p_qty, self.mats_list, self.recipe = p_name, p_qty, mats_list, {}
    @discord.ui.button(label="➕ 素材追加", style=discord.ButtonStyle.secondary)
    async def add_mat(self, interaction, button):
        view = discord.ui.View()
        sel = discord.ui.Select(placeholder="使用する素材を選択")
        for m in self.mats_list: sel.add_option(label=m[0], value=m[0])
        async def cb(i):
            modal = discord.ui.Modal(title="必要数入力")
            num = discord.ui.TextInput(label="1個あたり何個必要？", default="1")
            modal.add_item(num)
            async def sub(mi):
                self.recipe[sel.values[0]] = int(num.value)
                txt = "\n".join([f"・{k}: {v}個" for k,v in self.recipe.items()])
                await mi.response.edit_message(content=f"🔨 **制作確認: {self.p_name} x{self.p_qty}**\n{txt}", view=self)
            modal.on_submit = sub
            await i.response.send_modal(modal)
        sel.callback = cb
        view.add_item(sel)
        await interaction.response.send_message("使用する素材を選んでください：", view=view, ephemeral=True)

    @discord.ui.button(label="✅ 確定", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            for m, v in self.recipe.items():
                await db.execute("UPDATE materials SET current = current - ? WHERE name=?", (v*self.p_qty, m))
            await db.commit()
        await add_audit(interaction.user.id, "CRAFT", f"{self.p_name} x{self.p_qty}")
        await interaction.response.edit_message(content="✅ 在庫を減算し、制作を記録しました。", view=None)

class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🟢 出勤", style=discord.ButtonStyle.success, custom_id="g_in")
    async def in_btn(self, interaction, button):
        if OMNIS_ROLE_ID not in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message("⛔ あなたには出勤権限がありません。", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO work_logs VALUES (?,?,NULL)", (interaction.user.id, datetime.now()))
            await db.commit()
        await interaction.user.add_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message("🟢 出勤を記録しました。お疲れ様です！", ephemeral=True)

    @discord.ui.button(label="🔴 退勤", style=discord.ButtonStyle.danger, custom_id="g_out")
    async def out_btn(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            row = await (await db.execute("SELECT rowid FROM work_logs WHERE user_id=? AND end IS NULL", (interaction.user.id,))).fetchone()
            if not row: return await interaction.response.send_message("❌ 出勤データが見つかりません。", ephemeral=True)
            await db.execute("UPDATE work_logs SET end=? WHERE rowid=?", (datetime.now(), row[0]))
            await db.commit()
        await interaction.user.remove_roles(interaction.guild.get_role(WORK_ROLE_ID))
        await interaction.response.send_message("🔴 退勤を記録しました。ゆっくり休んでください！", ephemeral=True)

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="g_craft")
    async def craft_btn(self, interaction, button):
        if WORK_ROLE_ID not in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message("⛔ 出勤中のみ報告可能です。", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name FROM products")).fetchall()
            mats = await (await db.execute("SELECT name FROM materials")).fetchall()
        if not prods: return await interaction.response.send_message("❌ 登録されている商品がありません。", ephemeral=True)
        
        v = discord.ui.View()
        s = discord.ui.Select(placeholder="作った商品を選択")
        for p in prods: s.add_option(label=p[0], value=p[0])
        async def cb(i):
            m = discord.ui.Modal(title="制作数入力")
            q = discord.ui.TextInput(label="いくつ作りましたか？", default="1")
            m.add_item(q)
            async def sub(mi):
                await mi.response.send_message("素材情報を入力してください：", view=CraftProcessView(s.values[0], int(q.value), mats), ephemeral=True)
            m.on_submit = sub
            await i.response.send_modal(m)
        s.callback = cb
        v.add_item(s)
        await interaction.response.send_message("何を作りましたか？", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上登録", style=discord.ButtonStyle.secondary, custom_id="g_sale")
    async def sale_btn(self, interaction, button):
        if WORK_ROLE_ID not in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message("⛔ 出勤中のみ登録可能です。", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name, price FROM products")).fetchall()
        if not prods: return await interaction.response.send_message("❌ 商品データがありません。", ephemeral=True)
        
        v = discord.ui.View()
        s = discord.ui.Select(placeholder="販売した商品を選択")
        for p in prods: s.add_option(label=f"{p[0]} ({p[1]}{CURRENCY})", value=f"{p[0]}:{p[1]}")
        async def cb(i):
            name, price = s.values[0].split(":")
            await i.response.send_modal(SaleFinalizeModal(name, int(price)))
        s.callback = cb
        v.add_item(s)
        await interaction.response.send_message("何を売りましたか？", view=v, ephemeral=True)

class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="👤 メンバー管理", style=discord.ButtonStyle.success, custom_id="a_role")
    async def role_mgmt(self, interaction, button):
        v = discord.ui.View()
        b1 = discord.ui.Button(label="➕ 権限付与", style=discord.ButtonStyle.success)
        b1.callback = lambda i: i.response.send_modal(RoleManageModal("add"))
        b2 = discord.ui.Button(label="➖ 権限削除", style=discord.ButtonStyle.danger)
        b2.callback = lambda i: i.response.send_modal(RoleManageModal("del"))
        v.add_item(b1).add_item(b2)
        await interaction.response.send_message("メンバーの権限操作を選択：", view=v, ephemeral=True)

    @discord.ui.button(label="📋 監査ログ", style=discord.ButtonStyle.gray, custom_id="a_audit")
    async def view_audit(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, user_id, action, detail FROM audit_logs ORDER BY id DESC LIMIT 20")).fetchall()
        if not rows: return await interaction.response.send_message("ログはありません。", ephemeral=True)
        txt = "📜 **最近の監査ログ (20件)**\n```"
        for r in rows:
            u = interaction.guild.get_member(r[1])
            name = u.display_name if u else f"ID:{r[1]}"
            txt += f"[{r[0][5:16]}] {name} | {r[2]} | {r[3]}\n"
        await interaction.response.send_message(txt + "```", ephemeral=True)

    @discord.ui.button(label="📦 在庫管理", style=discord.ButtonStyle.secondary, custom_id="a_mat")
    async def mat_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            mats = await (await db.execute("SELECT name, current, threshold FROM materials")).fetchall()
        v = discord.ui.View()
        txt = "📦 **現在の素材在庫**\n" + "\n".join([f"・{m[0]}: {m[1]} / {m[2]}" for m in mats])
        v.add_item(discord.ui.Button(label="➕ 素材登録")).callback = lambda i: i.response.send_modal(MaterialAddModal())
        if mats:
            async def adj_sel(i, mode):
                v2 = discord.ui.View(); s = discord.ui.Select()
                for m in mats: s.add_option(label=m[0], value=m[0])
                s.callback = lambda si: si.response.send_modal(StockAdjustModal(s.values[0], mode))
                v2.add_item(s); await i.response.send_message("操作する素材を選択：", view=v2, ephemeral=True)
            
            b1 = discord.ui.Button(label="📥 補充"); b1.callback = lambda i: adj_sel(i, "plus")
            b2 = discord.ui.Button(label="📤 引き抜き"); b2.callback = lambda i: adj_sel(i, "minus")
            v.add_item(b1).add_item(b2)
            
            async def del_mat(i):
                v3 = discord.ui.View(); s = discord.ui.Select()
                for m in mats: s.add_option(label=m[0], value=m[0])
                async def scb(si):
                    async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM materials WHERE name=?", (s.values[0],)); await db.commit()
                    await add_audit(si.user.id, "MAT_DEL", s.values[0]); await si.response.send_message(f"🗑️ {s.values[0]} を削除しました。", ephemeral=True)
                s.callback = scb; v3.add_item(s); await i.response.send_message("完全に削除する素材を選択：", view=v3, ephemeral=True)
            
            b3 = discord.ui.Button(label="🗑️ 素材削除", style=discord.ButtonStyle.danger)
            b3.callback = del_mat; v.add_item(b3)
            
        await interaction.response.send_message(txt or "素材が登録されていません。", view=v, ephemeral=True)

    @discord.ui.button(label="⏰ 集計", style=discord.ButtonStyle.primary, custom_id="a_sum")
    async def work_sum(self, interaction, button):
        bonus = int(await get_config("hourly_bonus", "0"))
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT user_id, SUM(strftime('%s', end) - strftime('%s', start)) FROM work_logs WHERE end IS NOT NULL GROUP BY user_id")).fetchall()
        
        v = discord.ui.View()
        async def reset_cb(i):
            v2 = discord.ui.View()
            async def yes(yi):
                async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM work_logs"); await db.commit()
                await add_audit(yi.user.id, "RESET", "勤務データリセット")
                await yi.response.send_message("✅ 全員の勤務データをリセットしました。", ephemeral=True)
            b = discord.ui.Button(label="リセットを確定", style=discord.ButtonStyle.danger)
            b.callback = yes; v2.add_item(b)
            await i.response.send_message("⚠️ 本当に全てのデータを消去しますか？", view=v2, ephemeral=True)
        
        btn_reset = discord.ui.Button(label="🧹 データをリセット", style=discord.ButtonStyle.danger)
        btn_reset.callback = reset_cb; v.add_item(btn_reset)
        
        report = f"📊 **勤務集計結果** (時給ボーナス: {bonus}{CURRENCY}/h)\n"
        for u_id, sec in rows:
            m = interaction.guild.get_member(u_id)
            if m: report += f"👤 <@{m.id}>: **{int(sec/3600)}時間** (報酬: {int((sec/3600)*bonus)}{CURRENCY})\n"
        await interaction.response.send_message(report or "集計対象のデータがありません。", view=v, ephemeral=True)

    @discord.ui.button(label="💰 ボーナス設定", style=discord.ButtonStyle.secondary, custom_id="a_bonus")
    async def bonus_btn(self, interaction, button):
        await interaction.response.send_modal(BonusSetModal())

    @discord.ui.button(label="📝 商品管理", style=discord.ButtonStyle.gray, custom_id="a_prod")
    async def prod_mgmt(self, interaction, button):
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name, price FROM products")).fetchall()
        
        v = discord.ui.View()
        txt = "📝 **登録商品一覧**\n" + "\n".join([f"・{p[0]} ({p[1]}{CURRENCY})" for p in prods])
        
        btn_add = discord.ui.Button(label="➕ 商品追加", style=discord.ButtonStyle.success)
        btn_add.callback = lambda i: i.response.send_modal(ProductDefineModal())
        v.add_item(btn_add)
        
        if prods:
            async def d_cb(i):
                v2 = discord.ui.View(); s = discord.ui.Select(placeholder="削除する商品を選択")
                for p in prods: s.add_option(label=p[0], value=p[0])
                async def scb(si):
                    async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM products WHERE name=?", (s.values[0],)); await db.commit()
                    await add_audit(si.user.id, "PROD_DEL", s.values[0])
                    await si.response.send_message(f"🗑️ 商品「{s.values[0]}」を削除しました。", ephemeral=True)
                s.callback = scb; v2.add_item(s); await i.response.send_message("削除する商品を選択してください：", view=v2, ephemeral=True)
            
            btn_del = discord.ui.Button(label="🗑️ 商品削除", style=discord.ButtonStyle.danger)
            btn_del.callback = d_cb; v.add_item(btn_del)
            
        await interaction.response.send_message(txt or "商品は登録されていません。", view=v, ephemeral=True)

# ================= 5. メインロジック (クリーンアップ & 自動送信) =================

async def refresh_panels():
    """指定チャンネルの古いメッセージを掃除して最新パネルを送る"""
    channels = [
        (ADMIN_PANEL_CHANNEL_ID, AdminPanel(), "🔧 **管理者パネル**\n商会の設定、在庫管理、監査ログの確認、勤務集計を行います。"),
        (GENERAL_PANEL_CHANNEL_ID, GeneralPanel(), "🧾 **業務パネル**\n出勤・退勤の打刻、商品の制作報告、売上の登録を行います。")
    ]
    
    for channel_id, view, content in channels:
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                # チャンネル内のメッセージを一括削除
                await channel.purge(limit=100)
                # 新しいパネルを送信
                await channel.send(content, view=view)
            except Exception as e:
                print(f"Error refreshing channel {channel_id}: {e}")

@bot.event
async def on_ready():
    await init_db()
    # ボタンの反応を維持するための永続View登録
    bot.add_view(GeneralPanel())
    bot.add_view(AdminPanel())
    print(f"Logged in: {bot.user}")
    
    # 起動時に自動リフレッシュ
    await refresh_panels()
    print("Panels have been cleaned and refreshed.")

@bot.command()
async def setup(ctx):
    """手動でチャンネルを掃除してパネルを再送するコマンド"""
    if not is_admin(ctx.author): return
    await refresh_panels()
    await ctx.send("✅ パネルを最新の状態に更新しました。", delete_after=5)

if TOKEN:
    bot.run(TOKEN)
