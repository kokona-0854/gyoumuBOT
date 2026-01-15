import discord
from discord.ext import commands
import aiosqlite
from datetime import datetime
import os
import sys
from dotenv import load_dotenv

# ================= 1. 設定セクション =================
load_dotenv("/root/gyoumuBOT/.env")
TOKEN = os.getenv("DISCORD_TOKEN")

# ロールID
ADMIN_ROLE_ID = 1459388566760325318      
OMNIS_ROLE_ID = 1459208662055911538      
WORK_ROLE_ID = 1459209336076374068       

# チャンネルID
ADMIN_PANEL_CH = 1459371812310745171     
ITEM_PANEL_CH = 1461057553021538485      
GENERAL_PANEL_CH = 1458801073899966585   

# メンバー管理用ロール設定
ROLE_OPTIONS = {
    "オムニス権限": 1459208662055911538,
    "管理者ロール": 1459388566760325318,
    "会頭ロール": 1454307785717321738,
    "交易師ロール": 1454310938017661031,
    "従業員ロール": 1455242976258297917,
    "アルバイトロール": 1455243576337502228
}

DB_PATH = "omnis_system_v15.db"
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

# ================= 4. 商品パネル (ItemPanel) 修正版 =================
class ItemPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    async def interaction_check(self, i: discord.Interaction):
        if i.channel_id != ITEM_PANEL_CH: return False
        if any(r.id == ADMIN_ROLE_ID for r in i.user.roles): return True
        await i.response.send_message("❌ 管理ロールが必要です。", ephemeral=True); return False

    @discord.ui.button(label="商品・素材設定｜レシピ登録", style=discord.ButtonStyle.primary, custom_id="v16_it_reg")
    async def reg(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            p_rows = await (await db.execute("SELECT name FROM products")).fetchall()
            m_rows = await (await db.execute("SELECT name FROM materials")).fetchall()
        
        prods = [r[0] for r in p_rows]; mats = [r[0] for r in m_rows]
        view = discord.ui.View()

        # 基本登録
        async def add_p(idx, v):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR IGNORE INTO products (name) VALUES (?)", (v,)); await db.commit()
            await idx.response.send_message(f"✅ 商品 {v} 登録", ephemeral=True)
        async def add_m(idx, v):
            async with aiosqlite.connect(DB_PATH) as db: await db.execute("INSERT OR IGNORE INTO materials (name) VALUES (?)", (v,)); await db.commit()
            await idx.response.send_message(f"✅ 素材 {v} 登録", ephemeral=True)
        
        view.add_item(discord.ui.Button(label="商品追加", style=discord.ButtonStyle.success, row=0)).callback = lambda x: x.response.send_modal(GenericModal("商品", "名前", add_p))
        view.add_item(discord.ui.Button(label="素材追加", style=discord.ButtonStyle.success, row=0)).callback = lambda x: x.response.send_modal(GenericModal("素材", "名前", add_m))

        if prods:
            sel_p = discord.ui.Select(placeholder="個別操作（削除・レシピ登録・単価設定）", row=1)
            for p_name in prods: sel_p.add_option(label=f"商品: {p_name}", value=p_name)
            
            async def p_manage_cb(i2):
                target_p = sel_p.values[0]; v_sub = discord.ui.View()
                
                async def del_p(i3):
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("DELETE FROM products WHERE name=?", (target_p,))
                        await db.execute("DELETE FROM recipes WHERE product_name=?", (target_p,))
                        await db.commit()
                    await i3.response.send_message(f"🗑️ {target_p} を削除しました。", ephemeral=True)
                
                async def set_prc(i3, val):
                    async with aiosqlite.connect(DB_PATH) as db: 
                        await db.execute("UPDATE products SET price=? WHERE name=?", (int(val), target_p))
                        await db.commit()
                    await i3.response.send_message(f"💰 {target_p} の単価を {val}円 に設定しました。", ephemeral=True)

                v_sub.add_item(discord.ui.Button(label="❌ この商品を削除", style=discord.ButtonStyle.danger)).callback = del_p
                v_sub.add_item(discord.ui.Button(label="💰 単価設定", style=discord.ButtonStyle.primary)).callback = lambda x: x.response.send_modal(GenericModal("単価設定", "半角数字で入力", set_prc))
                
                # 【重要】レシピ登録機能（個別操作内）
                if mats:
                    sel_m = discord.ui.Select(placeholder="この商品に使う素材と個数を登録")
                    for m_name in mats: sel_m.add_option(label=f"素材: {m_name}", value=m_name)
                    async def r_cb(i4, qty):
                        async with aiosqlite.connect(DB_PATH) as db: 
                            await db.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (target_p, sel_m.values[0], int(qty)))
                            await db.commit()
                        await i4.response.send_message(f"✅ レシピ登録: {target_p} に {sel_m.values[0]} x{qty} を設定しました。", ephemeral=True)
                    sel_m.callback = lambda i5: i5.response.send_modal(GenericModal("必要個数", "1個作るのに必要な素材の数", r_cb))
                    v_sub.add_item(sel_m)
                
                await i2.response.send_message(f"【{target_p}】の個別設定メニュー:", view=v_sub, ephemeral=True)
            
            sel_p.callback = p_manage_cb; view.add_item(sel_p)
        await i.response.send_message("商品・素材のマスタ管理画面:", view=view, ephemeral=True)

    @discord.ui.button(label="在庫表示・在庫調整", style=discord.ButtonStyle.secondary, custom_id="v16_it_stock")
    async def stock(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            m = await (await db.execute("SELECT name, current FROM materials")).fetchall()
            p = await (await db.execute("SELECT name, current FROM products")).fetchall()
        
        # 商品と素材の両方の在庫を表示
        txt = "📦 **現在庫一覧**\n\n"
        txt += "**【制作済み商品在庫】**\n" + ("\n".join([f"・{x[0]}: `{x[1]}`個" for x in p]) if p else "なし")
        txt += "\n\n**【原材料・素材在庫】**\n" + ("\n".join([f"・{x[0]}: `{x[1]}`個" for x in m]) if m else "なし")
        
        view = discord.ui.View()
        # 素材の在庫調整
        if m:
            sel = discord.ui.Select(placeholder="素材の在庫を手動で調整（補充/引出）")
            for x in m: sel.add_option(label=f"素材: {x[0]}", value=x[0])
            async def adj(i2, v):
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE materials SET current = current + ? WHERE name=?", (int(v), sel.values[0]))
                    await db.commit()
                await i2.response.send_message(f"✅ {sel.values[0]} を {v}個 調整しました。", ephemeral=True)
            sel.callback = lambda i2: i2.response.send_modal(GenericModal("在庫調整", "+で補充 / -で減少", adj))
            view.add_item(sel)
        
        await i.response.send_message(txt, view=view, ephemeral=True)

# ================= 5. 管理パネル (AdminPanel) 修正版 =================
class AdminPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    async def interaction_check(self, i: discord.Interaction):
        if i.channel_id != ADMIN_PANEL_CH: return False
        if any(r.id == ADMIN_ROLE_ID for r in i.user.roles): return True
        await i.response.send_message("❌ 管理ロールが必要です。", ephemeral=True); return False

    @discord.ui.button(label="メンバー管理", style=discord.ButtonStyle.success, custom_id="v16_ad_mem")
    async def members(self, i, b):
        view = discord.ui.View(); sel = discord.ui.Select(placeholder="付与するロールを選択")
        for n, rid in ROLE_OPTIONS.items(): sel.add_option(label=n, value=str(rid))
        async def m_cb(i2):
            async def act(i3, uid):
                target = i3.guild.get_member(int(uid)); role = i3.guild.get_role(int(sel.values[0]))
                if target and role: await target.add_roles(role); await i3.response.send_message(f"✅ {target.display_name} に付与完了", ephemeral=True)
                else: await i3.response.send_message("❌ ユーザーまたはロールが見つかりません。", ephemeral=True)
            await i2.response.send_modal(GenericModal("ID入力", "ユーザーID", act))
        sel.callback = m_cb; view.add_item(sel); await i.response.send_message("ロール管理:", view=view, ephemeral=True)

    @discord.ui.button(label="集計/データリセット", style=discord.ButtonStyle.gray, custom_id="v16_ad_stat")
    async def stats(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            rank = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
            work = await (await db.execute("SELECT user_id, SUM(duration) FROM work_logs GROUP BY user_id")).fetchall()
        
        msg = "🏆 **売上ランキング**\n" + ("\n".join([f"<@{r[0]}>: {r[1]:,}円" for r in rank]) if rank else "データなし")
        msg += f"\n\n📊 **勤怠累計**\n" + ("\n".join([f"<@{w[0]}>: {w[1]//60}時間{w[1]%60}分" for w in work]) if work else "データなし")
        
        view = discord.ui.View()
        # 全体リセット機能の正常化
        async def reset_all(idx):
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM sales_ranking")
                await db.execute("DELETE FROM work_logs")
                await db.commit() # 反映
            await idx.response.send_message("✅ 全員の売上・勤怠データをリセットしました。", ephemeral=True)
        
        # 個人リセット機能の正常化
        async def reset_ind(idx, uid):
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM sales_ranking WHERE user_id=?", (int(uid),))
                await db.execute("DELETE FROM work_logs WHERE user_id=?", (int(uid),))
                await db.commit() # 反映
            await idx.response.send_message(f"✅ 指定ユーザー(<@{uid}>)のデータをリセットしました。", ephemeral=True)

        view.add_item(discord.ui.Button(label="全体リセット", style=discord.ButtonStyle.danger)).callback = reset_all
        view.add_item(discord.ui.Button(label="個人リセット", style=discord.ButtonStyle.secondary)).callback = lambda x: x.response.send_modal(GenericModal("リセット", "対象のユーザーID", reset_ind))
        await i.response.send_message(msg, view=view, ephemeral=True)

    @discord.ui.button(label="履歴ログ", style=discord.ButtonStyle.gray, custom_id="v16_ad_log")
    async def logs(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("SELECT created_at, user_id, action, detail FROM audit_logs ORDER BY id DESC LIMIT 15")).fetchall()
        txt = "📜 **履歴ログ**\n" + ("\n".join([f"`{r[0][5:16]}` <@{r[1]}> **{r[2]}**: {r[3]}" for r in rows]) if rows else "ログなし")
        await i.response.send_message(txt, ephemeral=True)

# ================= 6. 業務パネル (GeneralPanel) =================
class GeneralPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    @discord.ui.button(label="🟢 出勤/🔴 退勤", style=discord.ButtonStyle.success, custom_id="v15_gen_work")
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
                # 匿名メッセージ（ephemeral=True）
                await i.response.send_message(f"🔴 退勤しました。勤務時間: {diff//60}時間{diff%60}分", ephemeral=True)
            await db.commit()

    @discord.ui.button(label="🛠 制作報告", style=discord.ButtonStyle.primary, custom_id="v15_gen_craft")
    async def craft(self, i, b):
        if not any(r.id == WORK_ROLE_ID for r in i.user.roles): return await i.response.send_message("❌ 出勤中のみ可能です。", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            prods = [r[0] for r in await (await db.execute("SELECT name FROM products")).fetchall()]
        if not prods: return await i.response.send_message("❌ 商品が未登録です。", ephemeral=True)
        
        v = discord.ui.View(); sel = discord.ui.Select(placeholder="制作した商品を選択")
        for p in prods: sel.add_option(label=p, value=p)
        
        async def cb(i2, q):
            q = int(q)
            async with aiosqlite.connect(DB_PATH) as db:
                recipe = await (await db.execute("SELECT material_name, quantity FROM recipes WHERE product_name=?", (sel.values[0],))).fetchall()
                if not recipe: return await i2.response.send_message(f"❌ {sel.values[0]} のレシピが設定されていません。", ephemeral=True)
                
                # 在庫チェック
                for mn, mq in recipe:
                    cur = await (await db.execute("SELECT current FROM materials WHERE name=?", (mn,))).fetchone()
                    if not cur or cur[0] < (mq * q): return await i2.response.send_message(f"❌ 素材不足: {mn} (必要: {mq*q}, 現在: {cur[0] if cur else 0})", ephemeral=True)
                
                # 在庫変動
                for mn, mq in recipe: await db.execute("UPDATE materials SET current = current - ? WHERE name=?", (mq * q, mn))
                await db.execute("UPDATE products SET current = current + ? WHERE name=?", (q, sel.values[0]))
                await db.commit()
            
            await add_audit(i2.user.id, "制作", f"{sel.values[0]} x{q}")
            await i2.response.send_message(f"✅ {sel.values[0]} を {q} 個制作しました（素材を自動消費）。", ephemeral=True)
        
        sel.callback = lambda i2: i2.response.send_modal(GenericModal("制作数", "制作した個数（半角数字）", cb))
        v.add_item(sel); await i.response.send_message("制作物の報告:", view=v, ephemeral=True)

    @discord.ui.button(label="💰 売上報告", style=discord.ButtonStyle.success, custom_id="v15_gen_sale")
    async def sale(self, i, b):
        if not any(r.id == WORK_ROLE_ID for r in i.user.roles): return await i.response.send_message("❌ 出勤中のみ可能です。", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name, price FROM products")).fetchall()
        if not prods: return await i.response.send_message("❌ 商品がありません。", ephemeral=True)
        
        v = discord.ui.View(); sel = discord.ui.Select(placeholder="販売した商品を選択")
        for p, prc in prods: sel.add_option(label=f"{p} (単価: {prc}円)", value=f"{p}:{prc}")
        
        async def cb(i2, q):
            name, price = sel.values[0].split(":"); q = int(q); amt = int(price) * q
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await (await db.execute("SELECT current FROM products WHERE name=?", (name,))).fetchone()
                if not cur or cur[0] < q: return await i2.response.send_message(f"❌ 商品在庫が足りません (現在: {cur[0] if cur else 0})", ephemeral=True)
                
                await db.execute("UPDATE products SET current = current - ? WHERE name=?", (q, name))
                await db.execute("INSERT INTO sales_ranking (user_id, total_amount) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET total_amount = total_amount + ?", (i2.user.id, amt, amt))
                await db.commit()
            
            await add_audit(i2.user.id, "売上", f"{name} x{q} ({amt:,}円)")
            await i2.response.send_message(f"💰 売上報告完了: {name} x{q} ({amt:,}円)", ephemeral=True)
            
        sel.callback = lambda i2: i2.response.send_modal(GenericModal("売上数", "販売した個数（半角数字）", cb))
        v.add_item(sel); await i.response.send_message("売上の報告:", view=v, ephemeral=True)

# ================= 7. 起動・メンテナンスコマンド =================
@bot.event
async def on_ready():
    await init_db()
    bot.add_view(AdminPanel()); bot.add_view(ItemPanel()); bot.add_view(GeneralPanel())
    print(f"Logged in as {bot.user}")
    
    # チャンネルの掃除（再起動時の自動メッセージ削除）とパネル送信
    setup_data = [
        (ADMIN_PANEL_CH, AdminPanel(), "🔧 **管理者用・管理パネル**\n（ロール管理・統計・ログ確認用）"), 
        (ITEM_PANEL_CH, ItemPanel(), "📦 **管理者用・商品マスタパネル**\n（商品登録・レシピ・在庫調整用）"), 
        (GENERAL_PANEL_CH, GeneralPanel(), "🧾 **オムニス商会・業務パネル**\n（出退勤・制作報告・売上報告用）")
    ]
    
    for cid, view, title in setup_data:
        ch = bot.get_channel(cid)
        if ch:
            await ch.purge(limit=20) # 直近のメッセージを削除
            await ch.send(title, view=view)

@bot.command()
@commands.has_role(ADMIN_ROLE_ID)
async def restart(ctx):
    await ctx.send("♻️ Botを再起動しています...")
    os.execv(sys.executable, ['python'] + sys.argv)

bot.run(TOKEN)

