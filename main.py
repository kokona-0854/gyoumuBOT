import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime
import os
import sys
from dotenv import load_dotenv

# ================= 1. 設定セクション =================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ロールID
ADMIN_ROLE_ID = 1459388566760325318      # 管理ロール（管理・商品パネル操作用）
OMNIS_ROLE_ID = 1459208662055911538      # オムニス商会ロール（出退勤可能）
WORK_ROLE_ID = 1459209336076374068       # 出勤中ロール

# チャンネルID
ADMIN_PANEL_CH = 1459371812310745171     # 管理パネル
ITEM_PANEL_CH = 1461057553021538485      # 商品パネル
GENERAL_PANEL_CH = 1458801073899966585   # 業務パネル

# メンバー管理用ロール設定 (名前: ロールID)
ROLE_OPTIONS = {
    "オムニス権限": 1459208662055911538,
    "管理者ロール": 1459388566760325318,
    "会頭ロール": 1454307785717321738,
    "交易師ロール": 1454310938017661031,
    "従業員ロール": 1455242976258297917,
    "アルバイトロール": 1455243576337502228
}

DB_PATH = "omnis_system.db"
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= 2. データベース初期化 =================
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

# ================= 3. 共通UIコンポーネント =================
class GenericModal(discord.ui.Modal):
    def __init__(self, title, label, callback):
        super().__init__(title=title)
        self.input = discord.ui.TextInput(label=label)
        self.add_item(self.input)
        self.callback_func = callback
    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_func(interaction, self.input.value)

# ================= 4. 商品パネル (ItemPanel) =================
class ItemPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    async def interaction_check(self, i: discord.Interaction):
        if i.channel_id != ITEM_PANEL_CH: return False
        if any(r.id == ADMIN_ROLE_ID for r in i.user.roles): return True
        await i.response.send_message("❌ 管理ロールが必要です。", ephemeral=True); return False

    @discord.ui.button(label="商品・素材設定", style=discord.ButtonStyle.primary, custom_id="v1_it_reg")
    async def reg(self, i, b):
        view = discord.ui.View()
        async def add_p(idx, v):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR IGNORE INTO products (name) VALUES (?)", (v,)); await db.commit()
            await idx.response.send_message(f"✅ 商品 {v} を追加しました。", ephemeral=True)
        async def add_m(idx, v):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR IGNORE INTO materials (name) VALUES (?)", (v,)); await db.commit()
            await idx.response.send_message(f"✅ 素材 {v} を追加しました。", ephemeral=True)
        
        view.add_item(discord.ui.Button(label="商品追加", style=discord.ButtonStyle.success)).callback = lambda x: x.response.send_modal(GenericModal("商品", "名前", add_p))
        view.add_item(discord.ui.Button(label="素材追加", style=discord.ButtonStyle.success)).callback = lambda x: x.response.send_modal(GenericModal("素材", "名前", add_m))
        
        # レシピ設定用
        async with aiosqlite.connect(DB_PATH) as db:
            prods = [r[0] for r in await (await db.execute("SELECT name FROM products")).fetchall()]
            mats = [r[0] for r in await (await db.execute("SELECT name FROM materials")).fetchall()]

        if prods and mats:
            sel_p = discord.ui.Select(placeholder="商品を選択して価格/レシピ設定")
            for p in prods: sel_p.add_option(label=p, value=p)
            async def p_cb(i2):
                v3 = discord.ui.View()
                async def set_prc(i3, val):
                    async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE products SET price=? WHERE name=?", (int(val), sel_p.values[0])); await db.commit()
                    await i3.response.send_message(f"✅ {sel_p.values[0]} の価格を {val}円 にしました。", ephemeral=True)
                v3.add_item(discord.ui.Button(label="単価設定")).callback = lambda x: x.response.send_modal(GenericModal("単価", "金額", set_prc))
                
                sel_m = discord.ui.Select(placeholder="素材を選択してレシピ追加")
                for m in mats: sel_m.add_option(label=m, value=m)
                async def r_cb(i3, qty):
                    async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (sel_p.values[0], sel_m.values[0], int(qty))); await db.commit()
                    await i3.response.send_message(f"✅ {sel_p.values[0]} に {sel_m.values[0]} x{qty} を設定", ephemeral=True)
                sel_m.callback = lambda i4: i4.response.send_modal(GenericModal("個数", "必要数", r_cb))
                v3.add_item(sel_m)
                await i2.response.send_message(f"【{sel_p.values[0]}】の設定:", view=v3, ephemeral=True)
            sel_p.callback = p_cb; view.add_item(sel_p)
            
        await i.response.send_message("登録・レシピ:", view=view, ephemeral=True)

    @discord.ui.button(label="在庫・補充/引出", style=discord.ButtonStyle.secondary, custom_id="v1_it_stock")
    async def stock(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            m = await (await db.execute("SELECT name, current FROM materials")).fetchall()
            p = await (await db.execute("SELECT name, current FROM products")).fetchall()
        
        txt = "📦 **現在在庫**\n【素材】\n" + "\n".join([f"・{x[0]}: {x[1]}個" for x in m]) + "\n\n【商品】\n" + "\n".join([f"・{x[0]}: {x[1]}個" for x in p])
        view = discord.ui.View()
        if m:
            sel = discord.ui.Select(placeholder="補充/引出する素材を選択")
            for x in m: sel.add_option(label=x[0], value=x[0])
            async def adj(i2, v):
                async with aiosqlite.connect(DB_PATH) as db: await db.execute("UPDATE materials SET current = current + ? WHERE name=?", (int(v), sel.values[0])); await db.commit()
                await i2.response.send_message(f"✅ {sel.values[0]} を {v} 調整しました。", ephemeral=True)
            sel.callback = lambda i2: i2.response.send_modal(GenericModal("調整", "数 (+で補充, -で引出)", adj))
            view.add_item(sel)
        await i.response.send_message(txt, view=view, ephemeral=True)

# ================= 5. 管理パネル (AdminPanel) =================
class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    async def interaction_check(self, i: discord.Interaction):
        if i.channel_id != ADMIN_PANEL_CH: return False
        if any(r.id == ADMIN_ROLE_ID for r in i.user.roles): return True
        await i.response.send_message("❌ 管理ロールが必要です。", ephemeral=True); return False

    @discord.ui.button(label="メンバー管理", style=discord.ButtonStyle.success, custom_id="v1_ad_mem")
    async def members(self, i, b):
        view = discord.ui.View(); sel = discord.ui.Select(placeholder="付与するロールを選択")
        for n, rid in ROLE_OPTIONS.items(): sel.add_option(label=n, value=str(rid))
        async def m_cb(i2):
            async def act(i3, uid):
                target = i3.guild.get_member(int(uid)); role = i3.guild.get_role(int(sel.values[0]))
                await target.add_roles(role); await i3.response.send_message(f"✅ {target.display_name} に付与完了", ephemeral=True)
            await i2.response.send_modal(GenericModal("ID入力", "ユーザーID", act))
        sel.callback = m_cb; view.add_item(sel); await i.response.send_message("ロール管理:", view=view, ephemeral=True)

    @discord.ui.button(label="ランキング/勤怠集計", style=discord.ButtonStyle.gray, custom_id="v1_ad_stat")
    async def stats(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            rank = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
            work = await (await db.execute("SELECT user_id, SUM(duration) FROM work_logs GROUP BY user_id")).fetchall()
        
        msg = "🏆 **売上ランキング**\n" + "\n".join([f"<@{r[0]}>: {r[1]:,}円" for r in rank])
        msg += f"\n\n📊 **勤怠集計**\n" + "\n".join([f"<@{w[0]}>: {w[1]//60}時間{w[1]%60}分" for w in work])
        
        view = discord.ui.View()
        async def reset_all(idx):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM sales_ranking; DELETE FROM work_logs;"); await db.commit()
            await idx.response.send_message("✅ 全データをリセットしました。", ephemeral=True)
        async def reset_ind(idx, uid):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM sales_ranking WHERE user_id=?; DELETE FROM work_logs WHERE user_id=?", (int(uid), int(uid))); await db.commit()
            await idx.response.send_message(f"✅ <@{uid}> のデータをリセットしました。", ephemeral=True)

        view.add_item(discord.ui.Button(label="全体リセット", style=discord.ButtonStyle.danger)).callback = reset_all
        view.add_item(discord.ui.Button(label="個人リセット", style=discord.ButtonStyle.secondary)).callback = lambda x: x.response.send_modal(GenericModal("リセット", "ユーザーID", reset_ind))
        await i.response.send_message(msg, view=view, ephemeral=True)

    @discord.ui.button(label="履歴ログ", style=discord.ButtonStyle.gray, custom_id="v1_ad_log")
    async def logs(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, user_id, action, detail FROM audit_logs ORDER BY id DESC LIMIT 15")).fetchall()
        txt = "📜 **直近ログ**\n" + "\n".join([f"`{r[0][5:16]}` <@{r[1]}> **{r[2]}**: {r[3]}" for r in rows])
        await i.response.send_message(txt, ephemeral=True)

# ================= 6. 業務パネル (GeneralPanel) =================
class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    async def interaction_check(self, i: discord.Interaction):
        if i.channel_id != GENERAL_PANEL_CH: return False
        return True

    @discord.ui.button(label="🟢 出勤/🔴 退勤", style=discord.ButtonStyle.success, custom_id="v1_gen_work")
    async def work(self, i, b):
        if not any(r.id == OMNIS_ROLE_ID for r in i.user.roles):
            return await i.response.send_message("❌ オムニス商会ロールが必要です。", ephemeral=True)
        
        now = datetime.now()
        async with aiosqlite.connect(DB_PATH) as db:
            active = await (await db.execute("SELECT start FROM work_logs WHERE user_id=? AND end IS NULL", (i.user.id,))).fetchone()
            if not active:
                await db.execute("INSERT INTO work_logs (user_id, start) VALUES (?,?)", (i.user.id, now))
                await i.user.add_roles(i.guild.get_role(WORK_ROLE_ID))
                await i.response.send_message("🟢 出勤しました。", ephemeral=True)
            else:
                diff = int((now - datetime.strptime(active[0], "%Y-%m-%d %H:%M:%S.%f")).total_seconds() // 60)
                await db.execute("UPDATE work_logs SET end=?, duration=? WHERE user_id=? AND end IS NULL", (now, diff, i.user.id))
                await i.user.remove_roles(i.guild.get_role(WORK_ROLE_ID))
                await i.response.send_message(f"🔴 退勤しました。勤務時間: {diff//60}時間{diff%60}分", ephemeral=False) # 匿名メッセージではない仕様の場合はTrueへ
            await db.commit()

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="v1_gen_craft")
    async def craft(self, i, b):
        if not any(r.id == WORK_ROLE_ID for r in i.user.roles): return await i.response.send_message("❌ 出勤中のみ可能です。", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            prods = [r[0] for r in await (await db.execute("SELECT name FROM products")).fetchall()]
        if not prods: return await i.response.send_message("❌ 商品がありません。", ephemeral=True)
        
        v = discord.ui.View(); sel = discord.ui.Select(placeholder="商品を選択")
        for p in prods: sel.add_option(label=p, value=p)
        async def cb(i2, q):
            q = int(q)
            async with aiosqlite.connect(DB_PATH) as db:
                recipe = await (await db.execute("SELECT material_name, quantity FROM recipes WHERE product_name=?", (sel.values[0],))).fetchall()
                for mn, mq in recipe:
                    cur = await (await db.execute("SELECT current FROM materials WHERE name=?", (mn,))).fetchone()
                    if not cur or cur[0] < (mq * q): return await i2.response.send_message(f"❌ 素材不足: {mn}", ephemeral=True)
                for mn, mq in recipe: await db.execute("UPDATE materials SET current = current - ? WHERE name=?", (mq * q, mn))
                await db.execute("UPDATE products SET current = current + ? WHERE name=?", (q, sel.values[0]))
                await db.commit()
            await add_audit(i2.user.id, "制作", f"{sel.values[0]} x{q}"); await i2.response.send_message("✅ 制作完了", ephemeral=True)
        sel.callback = lambda i2: i2.response.send_modal(GenericModal("制作数", "個数", cb))
        v.add_item(sel); await i.response.send_message("報告:", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上報告", style=discord.ButtonStyle.success, custom_id="v1_gen_sale")
    async def sale(self, i, b):
        if not any(r.id == WORK_ROLE_ID for r in i.user.roles): return await i.response.send_message("❌ 出勤中のみ可能です。", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name, price FROM products")).fetchall()
        if not prods: return await i.response.send_message("❌ 商品がありません。", ephemeral=True)
        
        v = discord.ui.View(); sel = discord.ui.Select(placeholder="販売した商品")
        for p, prc in prods: sel.add_option(label=f"{p} ({prc}円)", value=f"{p}:{prc}")
        async def cb(i2, q):
            name, price = sel.values[0].split(":"); q = int(q); amt = int(price) * q
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await (await db.execute("SELECT current FROM products WHERE name=?", (name,))).fetchone()
                if not cur or cur[0] < q: return await i2.response.send_message("❌ 商品在庫不足", ephemeral=True)
                await db.execute("UPDATE products SET current = current - ? WHERE name=?", (q, name))
                await db.execute("INSERT INTO sales_ranking (user_id, total_amount) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET total_amount = total_amount + ?", (i2.user.id, amt, amt))
                await db.commit()
            await add_audit(i2.user.id, "売上", f"{name} x{q} ({amt:,}円)"); await i2.response.send_message(f"💰 売上完了: {amt:,}円", ephemeral=True)
        sel.callback = lambda i2: i2.response.send_modal(GenericModal("売上数", "個数", cb))
        v.add_item(sel); await i.response.send_message("報告:", view=v, ephemeral=True)

# ================= 7. 起動・メンテナンスコマンド =================
@bot.event
async def on_ready():
    await init_db()
    bot.add_view(AdminPanel()); bot.add_view(ItemPanel()); bot.add_view(GeneralPanel())
    print(f"Logged in as {bot.user}")
    
    # チャンネルの掃除とパネル送信
    for cid, view, title in [(ADMIN_PANEL_CH, AdminPanel(), "🔧 **管理パネル**"), 
                             (ITEM_PANEL_CH, ItemPanel(), "📦 **商品・在庫パネル**"), 
                             (GENERAL_PANEL_CH, GeneralPanel(), "🧾 **業務パネル**")]:
        ch = bot.get_channel(cid)
        if ch:
            await ch.purge(limit=10)
            await ch.send(title, view=view)

@bot.command()
@commands.has_role(ADMIN_ROLE_ID)
async def restart(ctx):
    await ctx.send("♻️ 再起動しています...")
    os.execv(sys.executable, ['python'] + sys.argv)

bot.run(TOKEN)
