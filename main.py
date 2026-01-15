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

    @discord.ui.button(label="商品・素材マスタ管理", style=discord.ButtonStyle.primary, custom_id="v21_it_master")
    async def reg(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            p_rows = await (await db.execute("SELECT name FROM products")).fetchall()
            m_rows = await (await db.execute("SELECT name FROM materials")).fetchall()
        
        view = discord.ui.View()
        
        # --- 商品・素材追加（ここは正常動作中） ---
        async def add_p_cb(idx, v):
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR IGNORE INTO products (name, current, price) VALUES (?, 0, 0)", (v,))
                await db.commit()
            await idx.response.send_message(f"✅ 商品【{v}】を登録しました。", ephemeral=True)
        
        async def add_m_cb(idx, v):
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR IGNORE INTO materials (name, current) VALUES (?, 0)", (v,))
                await db.commit()
            await idx.response.send_message(f"✅ 素材【{v}】を登録しました。", ephemeral=True)

        btn_p = discord.ui.Button(label="➕商品追加", style=discord.ButtonStyle.success, row=0)
        btn_p.callback = lambda x: x.response.send_modal(GenericModal("商品登録", "名前", add_p_cb))
        btn_m = discord.ui.Button(label="➕素材追加", style=discord.ButtonStyle.success, row=0)
        btn_m.callback = lambda x: x.response.send_modal(GenericModal("素材登録", "名前", add_m_cb))
        view.add_item(btn_p); view.add_item(btn_m)

        # --- 商品個別操作プルダウン ---
        if p_rows:
            sel_p = discord.ui.Select(placeholder="商品の設定（単価・削除）を選択", row=1)
            for r in p_rows[:25]: sel_p.add_option(label=f"商品: {r[0]}", value=r[0])
            
            async def p_manage_dispatch(i2):
                target = sel_p.values[0]
                # 専用のViewを呼び出す
                await i2.response.send_message(f"📦 【{target}】の操作を選択してください：", 
                                             view=ProductControlView(target), ephemeral=True)
            sel_p.callback = p_manage_dispatch
            view.add_item(sel_p)

        # --- 素材削除プルダウン ---
        if m_rows:
            sel_m = discord.ui.Select(placeholder="素材を削除する", row=2)
            for r in m_rows[:25]: sel_m.add_option(label=f"素材削除: {r[0]}", value=r[0])
            
            async def m_del_cb(i2):
                target = sel_m.values[0]
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM materials WHERE name=?", (target,))
                    await db.execute("DELETE FROM recipes WHERE material_name=?", (target,))
                    await db.commit()
                await i2.response.send_message(f"🗑️ 素材【{target}】を削除しました。", ephemeral=True)
            sel_m.callback = m_del_cb
            view.add_item(sel_m)

        await i.response.send_message("⚙️ **マスタ管理メニュー**", view=view, ephemeral=True)

    # 3. 素材補充・引き出し
    @discord.ui.button(label="素材補充・引き出し", style=discord.ButtonStyle.secondary, custom_id="v19_it_m_adj")
    async def mat_adj(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            mats = await (await db.execute("SELECT name, current FROM materials")).fetchall()
        
        if not mats: return await i.response.send_message("❌ 素材が登録されていません。", ephemeral=True)
        
        view = discord.ui.View(); sel = discord.ui.Select(placeholder="対象の素材を選択")
        for r in mats[:25]: sel.add_option(label=f"{r[0]} (現在: {r[1]}個)", value=r[0])
        
        async def adj_cb(i2, val):
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE materials SET current = current + ? WHERE name=?", (int(val), sel.values[0]))
                await db.commit()
            await i2.response.send_message(f"✅ {sel.values[0]} を {val} 個調整しました。", ephemeral=True)
            
        sel.callback = lambda i2: i2.response.send_modal(GenericModal("在庫調整", "+で補充 / -で減少", adj_cb))
        view.add_item(sel); await i.response.send_message("📦 **素材在庫の直接調整**", view=view, ephemeral=True)

    # 4. 在庫表示
    @discord.ui.button(label="在庫表示", style=discord.ButtonStyle.gray, custom_id="v19_it_stock")
    async def stock_view(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            m = await (await db.execute("SELECT name, current FROM materials")).fetchall()
            p = await (await db.execute("SELECT name, current FROM products")).fetchall()
        
        txt = "📦 **現在庫一覧**\n\n**【商品（制作済み）】**\n"
        txt += ("\n".join([f"・{x[0]}: `{x[1]}`個 (単価:{x[2] if len(x)>2 else 0}円)" for x in p]) if p else "なし")
        txt += "\n\n**【素材（原材料）】**\n"
        txt += ("\n".join([f"・{x[0]}: `{x[1]}`個" for x in m]) if m else "なし")
        await i.response.send_message(txt, ephemeral=True)

# ================= 4.5. 商品個別操作用サブView =================
class ProductControlView(discord.ui.View):
    def __init__(self, target_product: str):
        super().__init__(timeout=180)
        self.target = target_product

    @discord.ui.button(label="💰 単価設定", style=discord.ButtonStyle.primary)
    async def set_price(self, i: discord.Interaction, b: discord.ui.Button):
        async def set_p_final(idx, val):
            try:
                price_val = int(val)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE products SET price=? WHERE name=?", (price_val, self.target))
                    await db.commit()
                await idx.response.send_message(f"✅ {self.target} の単価を {price_val}円 に設定しました。", ephemeral=True)
            except ValueError:
                await idx.response.send_message("❌ 半角数字で入力してください。", ephemeral=True)
        
        await i.response.send_modal(GenericModal(f"{self.target}の単価設定", "金額を入力", set_p_final))

    @discord.ui.button(label="❌ 商品を削除", style=discord.ButtonStyle.danger)
    async def delete_prod(self, i: discord.Interaction, b: discord.ui.Button):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM products WHERE name=?", (self.target,))
            await db.execute("DELETE FROM recipes WHERE product_name=?", (self.target,))
            await db.commit()
        await i.response.send_message(f"🗑️ 商品【{self.target}】をマスタから削除しました。", ephemeral=True)

    # 2. レシピボタン（制作時の素材・個数設定）
    @discord.ui.button(label="レシピ設定", style=discord.ButtonStyle.success, custom_id="v19_it_recipe")
    async def recipe(self, i, b):
        async with aiosqlite.connect(DB_PATH) as db:
            prods = await (await db.execute("SELECT name FROM products")).fetchall()
            mats = await (await db.execute("SELECT name FROM materials")).fetchall()
        
        if not prods or not mats: return await i.response.send_message("❌ 商品と素材の両方を登録してください。", ephemeral=True)
        
        view = discord.ui.View(); sel_p = discord.ui.Select(placeholder="レシピを設定する商品を選択")
        for r in prods[:25]: sel_p.add_option(label=f"商品: {r[0]}", value=r[0])
        
        async def p_sel_cb(i2):
            target_p = sel_p.values[0]; v2 = discord.ui.View()
            sel_m = discord.ui.Select(placeholder=f"{target_p} に使う素材を選択")
            for r in mats[:25]: sel_m.add_option(label=f"素材: {r[0]}", value=r[0])
            
            async def r_final(i3, qty):
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (target_p, sel_m.values[0], int(qty)))
                    await db.commit()
                await i3.response.send_message(f"✅ {target_p} 1個につき {sel_m.values[0]} を {qty}個 使用するように設定しました。", ephemeral=True)
            
            sel_m.callback = lambda i4: i4.response.send_modal(GenericModal("個数設定", "1個制作に必要な数", r_final))
            v2.add_item(sel_m); await i2.response.send_message(f"【{target_p}】の素材を指定：", view=v2, ephemeral=True)
        
        sel_p.callback = p_sel_cb; view.add_item(sel_p)
        await i.response.send_message("📜 **レシピ設定（制作報告と連動）**", view=view, ephemeral=True)

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

    @discord.ui.button(label="集計/データリセット", style=discord.ButtonStyle.gray, custom_id="v22_ad_stat")
    async def stats(self, i: discord.Interaction, b: discord.ui.Button):
        async with aiosqlite.connect(DB_PATH) as db:
            rank = await (await db.execute("SELECT user_id, total_amount FROM sales_ranking ORDER BY total_amount DESC")).fetchall()
            work = await (await db.execute("SELECT user_id, SUM(duration) FROM work_logs GROUP BY user_id")).fetchall()
        
        msg = "🏆 **売上ランキング**\n" + ("\n".join([f"<@{r[0]}>: {r[1]:,}円" for r in rank]) if rank else "データなし")
        msg += f"\n\n📊 **勤怠累計**\n" + ("\n".join([f"<@{w[0]}>: {w[1]//60}時間{w[1]%60}分" for w in work]) if work else "データなし")
        
        # リセット専用のViewを呼び出すことで動作を確定させる
        await i.response.send_message(msg, view=DataResetView(), ephemeral=True)

# ================= 4.6. リセット操作専用View =================
class DataResetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="⚠️ 全体リセット", style=discord.ButtonStyle.danger)
    async def reset_all_btn(self, i: discord.Interaction, b: discord.ui.Button):
        # 誤操作防止の確認などは入れず、即時実行します
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM sales_ranking")
            await db.execute("DELETE FROM work_logs")
            await db.commit()
        await i.response.send_message("✅ 全員の売上・勤怠データをリセットしました。", ephemeral=True)

    @discord.ui.button(label="👤 個人リセット", style=discord.ButtonStyle.secondary)
    async def reset_ind_btn(self, i: discord.Interaction, b: discord.ui.Button):
        async def reset_ind_callback(idx, uid):
            try:
                target_uid = int(uid)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM sales_ranking WHERE user_id=?", (target_uid,))
                    await db.execute("DELETE FROM work_logs WHERE user_id=?", (target_uid,))
                    await db.commit()
                await idx.response.send_message(f"✅ 指定ユーザー(<@{target_uid}>)のデータをリセットしました。", ephemeral=True)
            except ValueError:
                await idx.response.send_message("❌ 正しいユーザーID（数字）を入力してください。", ephemeral=True)

        await i.response.send_modal(GenericModal("個人データリセット", "対象のユーザーIDを入力", reset_ind_callback))

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

