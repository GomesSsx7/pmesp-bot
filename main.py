import discord
from discord.ext import commands
import sqlite3
import asyncio
from datetime import datetime
import os

# --- Configurações do Banco de Dados ---
# Define o caminho para a base de dados ficar salva corretamente na nuvem
DB_PATH = os.path.join(os.path.dirname(__file__), "ponto.db")

def setup_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Cria a tabela (mantenha as colunas que você já tinha aqui)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registro_ponto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            inicio TEXT,
            fim TEXT,
            status TEXT
        )
    """)
    conn.commit()

    # 2. Tenta adicionar a coluna que estava faltando
    try:
        cursor.execute("ALTER TABLE registro_ponto ADD COLUMN duracao_segundos INTEGER")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # A coluna já existe, ignora

    conn.close()

setup_db()

def get_ponto_ativo(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, status, inicio FROM registro_ponto WHERE user_id = ? AND status != 'finalizado'", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def get_total_horas(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(duracao_segundos) FROM registro_ponto WHERE user_id = ? AND status = 'finalizado'", (user_id,))
    total_segundos = cursor.fetchone()[0] or 0
    conn.close()
    
    horas = total_segundos // 3600
    minutos = (total_segundos % 3600) // 60
    return horas, minutos

async def finalizar_ponto_usuario(user, motivo="Fechamento manual"):
    ponto = get_ponto_ativo(user.id)
    if not ponto:
        return False, "Você não possui um ponto ativo no momento."

    agora_dt = datetime.now()
    inicio_dt = datetime.strptime(ponto[2], "%Y-%m-%d %H:%M:%S")
    duracao = int((agora_dt - inicio_dt).total_seconds())

    agora_str = agora_dt.strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE registro_ponto SET fim = ?, duracao_segundos = ?, status = 'finalizado' WHERE id = ?", 
                   (agora_str, duracao, ponto[0]))
    conn.commit()
    conn.close()

    if user.id in autoclose_tasks:
        del autoclose_tasks[user.id]

    horas = duracao // 3600
    minutos = (duracao % 3600) // 60
    
    return True, f"🔴 **Ponto finalizado** por {user.mention} às **{agora_str[11:19]}**.\n⏱️ **Duração do patrulhamento:** {horas}h {minutos}m (`Motivo: {motivo}`)"

# --- Configurações Iniciais do Bot ---
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
autoclose_tasks = {}

# --- Painel com Botões Interativos ---

class PainelPontoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Iniciar", style=discord.ButtonStyle.green, custom_id="btn_iniciar", emoji="🟢")
    async def iniciar(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        ponto = get_ponto_ativo(user.id)
        
        if ponto:
            await interaction.response.send_message(f"⚠️ {user.mention}, você já tem um ponto em andamento (**{ponto[1]}**).", ephemeral=True)
            return

        if user.id in autoclose_tasks:
            autoclose_tasks[user.id].cancel()
            del autoclose_tasks[user.id]

        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO registro_ponto (user_id, username, inicio, status) VALUES (?, ?, ?, 'trabalhando')", 
                       (user.id, str(user), agora))
        conn.commit()
        conn.close()

        await interaction.response.send_message(f"🟢 **Ponto iniciado** por {user.mention} às **{agora[11:19]}**.", ephemeral=False)

    @discord.ui.button(label="Pausar", style=discord.ButtonStyle.secondary, custom_id="btn_pausar", emoji="🟡")
    async def pausar(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        ponto = get_ponto_ativo(user.id)
        
        if not ponto or ponto[1] != 'trabalhando':
            await interaction.response.send_message(f"⚠️ {user.mention}, você não está em serviço ativo no momento.", ephemeral=True)
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE registro_ponto SET status = 'pausado' WHERE id = ?", (ponto[0],))
        conn.commit()
        conn.close()

        await interaction.response.send_message(f"🟡 **Ponto pausado** por {user.mention}.", ephemeral=False)

    @discord.ui.button(label="Finalizar", style=discord.ButtonStyle.red, custom_id="btn_finalizar", emoji="🔴")
    async def finalizar(self, interaction: discord.Interaction, button: discord.ui.Button):
        sucesso, msg = await finalizar_ponto_usuario(interaction.user, motivo="Finalizado via painel")
        await interaction.response.send_message(msg, ephemeral=not sucesso)

    @discord.ui.button(label="Horas", style=discord.ButtonStyle.primary, custom_id="btn_horas", emoji="📊")
    async def horas(self, interaction: discord.Interaction, button: discord.ui.Button):
        horas, minutos = get_total_horas(interaction.user.id)
        await interaction.response.send_message(f"👮‍♂️ {interaction.user.mention}, o seu total acumulado de patrulhamento é de **{horas}h {minutos}m**.", ephemeral=True)

# --- Evento Bot Online ---

@bot.event
async def on_ready():
    bot.add_view(PainelPontoView())
    print(f"Bot PMESP Ponto online como {bot.user}")

# --- Comandos do Bot ---

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_ponto(ctx):
    await ctx.message.delete()
    embed = discord.Embed(
        title="Bate-Ponto PMESP",
        description="Clique nos botões abaixo para gerenciar o seu turno de patrulhamento:\n\n"
                    "🟢 **Iniciar:** Inicia a contagem do seu ponto.\n"
                    "🟡 **Pausar:** Coloca seu ponto em pausa.\n"
                    "🔴 **Finalizar:** Encerra o seu expediente e calcula o tempo.\n"
                    "📊 **Horas:** Consulta o seu total de horas acumuladas.",
        color=discord.Color.dark_blue()
    )
    embed.set_footer(text="PMESP Bate Ponto • Sistema Automático")
    await ctx.send(embed=embed, view=PainelPontoView())

@bot.command()
@commands.has_permissions(administrator=True)
async def ver_pontos(ctx):
    """Exibe todos os oficiais que estão com o ponto aberto no momento"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, status, inicio FROM registro_ponto WHERE status != 'finalizado'")
    ativos = cursor.fetchall()
    conn.close()

    if not ativos:
        await ctx.send("ℹ️ Não há nenhum oficial com o ponto aberto no momento.")
        return

    embed = discord.Embed(
        title="🟢 Oficiais Atualmente em Serviço",
        description="Lista de oficiais com ponto aberto neste momento:",
        color=discord.Color.green()
    )

    texto = ""
    for user_id, status, inicio in ativos:
        emoji_status = "🟢 em patrulha" if status == "trabalhando" else "🟡 pausado"
        hora_inicio = inicio[11:16]
        texto += f"• <@{user_id}> - Status: **{emoji_status}** (Início: `{hora_inicio}`)\n"

    embed.add_field(name="Oficiais Ativos", value=texto, inline=False)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def horas_membro(ctx, member: discord.Member):
    horas, minutos = get_total_horas(member.id)
    embed = discord.Embed(
        title="📊 Consulta de Horas de Patrulhamento",
        description=f"**Membro:** {member.mention}\n**Total Acumulado:** `{horas}h {minutos}m`",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def relatorio(ctx):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, username, SUM(duracao_segundos) as total
        FROM registro_ponto
        WHERE status = 'finalizado'
        GROUP BY user_id
        ORDER BY total DESC
    """)
    resultados = cursor.fetchall()
    conn.close()

    if not resultados:
        await ctx.send("📋 Nenhum registro de ponto finalizado foi encontrado.")
        return

    embed = discord.Embed(
        title="📋 Relatório Geral de Patrulhamento - PMESP",
        description="Abaixo está a lista consolidada com o total de horas acumuladas de todos os oficiais:",
        color=discord.Color.gold()
    )

    texto_relatorio = ""
    for idx, (user_id, username, total_segundos) in enumerate(resultados, 1):
        horas = (total_segundos or 0) // 3600
        minutos = ((total_segundos or 0) % 3600) // 60
        texto_relatorio += f"`#{idx}` <@{user_id}> - **{horas}h {minutos}m**\n"

    embed.add_field(name="Oficiais / Horas Acumuladas", value=texto_relatorio, inline=False)
    embed.set_footer(text=f"Solicitado por {ctx.author.display_name}")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def zerar_horas(ctx, member: discord.Member):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM registro_ponto WHERE user_id = ?", (member.id,))
    conn.commit()
    conn.close()
    await ctx.send(f"✅ As horas acumuladas do oficial {member.mention} foram zeradas com sucesso.")

@bot.command()
@commands.has_permissions(administrator=True)
async def zerar_tudo(ctx):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM registro_ponto")
    conn.commit()
    conn.close()
    await ctx.send("🚨 **Atenção:** Todo o histórico de horas e pontos do servidor foi zerado.")

# --- Monitoramento de Desconexão da Call (3 minutos) ---

@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel is not None and after.channel is None:
        ponto = get_ponto_ativo(member.id)
        if ponto:
            if member.id in autoclose_tasks:
                autoclose_tasks[member.id].cancel()
            autoclose_tasks[member.id] = asyncio.create_task(agendar_fechamento_automatico(member))

    elif before.channel is None and after.channel is not None:
        if member.id in autoclose_tasks:
            autoclose_tasks[member.id].cancel()
            del autoclose_tasks[member.id]

async def agendar_fechamento_automatico(member):
    try:
        await asyncio.sleep(180)
        sucesso, msg = await finalizar_ponto_usuario(member, motivo="Desconexão da call (>3 min)")
        if sucesso:
            try:
                await member.send(f"⚠️ O seu ponto foi finalizado automaticamente por ter saído da call há mais de 3 minutos.\n{msg}")
            except Exception:
                pass
    except asyncio.CancelledError:
        pass

# COLOQUE SEU TOKEN DO DISCORD ABAIXO
import os

TOKEN = os.environ.get("DISCORD_TOKEN")
bot.run(TOKEN)