#!/usr/bin/env python3
"""
Script de Rotacao de Secrets

Uso:
    python scripts/rotate_secrets.py

Funcionalidades:
    1. Backup do .env atual
    2. Gera novo JWT_SECRET seguro
    3. Atualiza o .env automaticamente
    4. Instrucoes para TURSO_AUTH_TOKEN
    5. Opcao de reiniciar servidor
"""

import os
import sys
import secrets
import shutil
from datetime import datetime
from pathlib import Path

# Cores para terminal
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header():
    print(f"""
{Colors.BOLD}{Colors.BLUE}╔══════════════════════════════════════════════════════════╗
║           ROTACAO DE SECRETS - NANDA BACKEND             ║
╚══════════════════════════════════════════════════════════╝{Colors.END}
""")

def print_success(msg):
    print(f"{Colors.GREEN}✓ {msg}{Colors.END}")

def print_warning(msg):
    print(f"{Colors.YELLOW}⚠ {msg}{Colors.END}")

def print_error(msg):
    print(f"{Colors.RED}✗ {msg}{Colors.END}")

def print_info(msg):
    print(f"{Colors.BLUE}ℹ {msg}{Colors.END}")

def get_env_path():
    """Retorna o caminho do arquivo .env"""
    script_dir = Path(__file__).parent.parent
    return script_dir / ".env"

def backup_env(env_path):
    """Cria backup do .env atual"""
    if not env_path.exists():
        print_error(f"Arquivo .env nao encontrado em {env_path}")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = env_path.parent / f".env.backup_{timestamp}"

    shutil.copy(env_path, backup_path)
    print_success(f"Backup criado: {backup_path}")
    return backup_path

def generate_jwt_secret():
    """Gera um novo JWT_SECRET seguro"""
    return secrets.token_hex(32)

def read_env_file(env_path):
    """Le o arquivo .env e retorna como dict"""
    env_vars = {}
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key] = value
    return env_vars

def write_env_file(env_path, env_vars, original_content):
    """Escreve o arquivo .env mantendo comentarios e ordem"""
    with open(env_path, 'r') as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            key = stripped.split('=', 1)[0]
            if key in env_vars:
                new_lines.append(f"{key}={env_vars[key]}\n")
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    with open(env_path, 'w') as f:
        f.writelines(new_lines)

def update_jwt_secret(env_path, new_secret):
    """Atualiza o JWT_SECRET no arquivo .env"""
    with open(env_path, 'r') as f:
        content = f.read()

    lines = content.split('\n')
    new_lines = []
    updated = False

    for line in lines:
        if line.strip().startswith('JWT_SECRET='):
            new_lines.append(f'JWT_SECRET={new_secret}')
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f'JWT_SECRET={new_secret}')

    with open(env_path, 'w') as f:
        f.write('\n'.join(new_lines))

    return updated

def show_current_status(env_path):
    """Mostra status atual dos secrets"""
    env_vars = read_env_file(env_path)

    print(f"\n{Colors.BOLD}Status Atual:{Colors.END}")
    print("-" * 50)

    # JWT_SECRET
    jwt_secret = env_vars.get('JWT_SECRET', '')
    if jwt_secret:
        # Mostra apenas os primeiros 20 caracteres
        masked = jwt_secret[:20] + "..." if len(jwt_secret) > 20 else jwt_secret

        # Verifica se e fraco
        weak_patterns = ['secret', 'password', 'crm', 'test', 'dev', '123']
        is_weak = any(p in jwt_secret.lower() for p in weak_patterns) or len(jwt_secret) < 32

        status = f"{Colors.RED}FRACO{Colors.END}" if is_weak else f"{Colors.GREEN}OK{Colors.END}"
        print(f"JWT_SECRET: {masked} [{status}]")
    else:
        print(f"JWT_SECRET: {Colors.RED}NAO CONFIGURADO{Colors.END}")

    # TURSO_AUTH_TOKEN
    turso_token = env_vars.get('TURSO_AUTH_TOKEN', '')
    if turso_token:
        masked = turso_token[:20] + "..." if len(turso_token) > 20 else turso_token
        print(f"TURSO_AUTH_TOKEN: {masked} [{Colors.GREEN}CONFIGURADO{Colors.END}]")
    else:
        print(f"TURSO_AUTH_TOKEN: {Colors.RED}NAO CONFIGURADO{Colors.END}")

    print("-" * 50)
    return env_vars

def confirm(prompt):
    """Pede confirmacao do usuario"""
    while True:
        response = input(f"{prompt} (s/n): ").strip().lower()
        if response in ['s', 'sim', 'y', 'yes']:
            return True
        elif response in ['n', 'nao', 'no']:
            return False
        print("Por favor, responda 's' ou 'n'")

def main():
    print_header()

    env_path = get_env_path()

    if not env_path.exists():
        print_error(f"Arquivo .env nao encontrado em {env_path}")
        print_info("Crie o arquivo .env primeiro")
        sys.exit(1)

    # Mostrar status atual
    env_vars = show_current_status(env_path)

    print(f"\n{Colors.BOLD}O que deseja fazer?{Colors.END}")
    print("1. Rotacionar JWT_SECRET")
    print("2. Ver instrucoes para TURSO_AUTH_TOKEN")
    print("3. Rotacionar ambos")
    print("4. Sair")

    choice = input("\nEscolha (1-4): ").strip()

    if choice == '4':
        print_info("Operacao cancelada")
        sys.exit(0)

    # Backup
    if choice in ['1', '3']:
        print(f"\n{Colors.BOLD}Passo 1: Backup{Colors.END}")
        backup_path = backup_env(env_path)
        if not backup_path:
            sys.exit(1)

    # Rotacionar JWT_SECRET
    if choice in ['1', '3']:
        print(f"\n{Colors.BOLD}Passo 2: Rotacionar JWT_SECRET{Colors.END}")

        new_secret = generate_jwt_secret()
        print_info(f"Novo secret gerado: {new_secret[:20]}...")

        print_warning("ATENCAO: Todos os usuarios logados serao desconectados!")

        if confirm("Deseja atualizar o JWT_SECRET?"):
            update_jwt_secret(env_path, new_secret)
            print_success("JWT_SECRET atualizado com sucesso!")

            print(f"\n{Colors.YELLOW}Novo JWT_SECRET (guarde em local seguro):{Colors.END}")
            print(f"{Colors.BOLD}{new_secret}{Colors.END}")
        else:
            print_info("JWT_SECRET nao foi alterado")

    # Instrucoes TURSO_AUTH_TOKEN
    if choice in ['2', '3']:
        print(f"\n{Colors.BOLD}Instrucoes para TURSO_AUTH_TOKEN:{Colors.END}")
        print("-" * 50)
        print("""
1. Acesse o console Turso:
   https://turso.tech/app

2. Selecione seu projeto/database

3. Va em "Settings" ou "Tokens"

4. Clique em "Create Token" ou "Generate New Token"

5. Copie o novo token

6. Atualize o .env:
   TURSO_AUTH_TOKEN=<seu_novo_token>

7. (Opcional) Revogue o token antigo no console

8. Reinicie o servidor
""")
        print("-" * 50)

        if confirm("Deseja abrir o console Turso no navegador?"):
            import webbrowser
            webbrowser.open("https://turso.tech/app")

    # Reiniciar servidor
    print(f"\n{Colors.BOLD}Passo Final: Reiniciar Servidor{Colors.END}")
    print_warning("O servidor precisa ser reiniciado para aplicar as mudancas")

    if confirm("Deseja reiniciar o servidor agora?"):
        print_info("Tentando reiniciar...")

        # Tentar encontrar e reiniciar o processo
        import subprocess

        # Verificar se usa PM2
        result = subprocess.run(['which', 'pm2'], capture_output=True, text=True)
        if result.returncode == 0:
            subprocess.run(['pm2', 'restart', 'all'], capture_output=True)
            print_success("Servidor reiniciado via PM2")
        else:
            # Verificar se usa systemd
            result = subprocess.run(['systemctl', 'is-active', 'crm-backend'], capture_output=True, text=True)
            if 'active' in result.stdout:
                subprocess.run(['sudo', 'systemctl', 'restart', 'crm-backend'])
                print_success("Servidor reiniciado via systemd")
            else:
                print_warning("Nao foi possivel reiniciar automaticamente")
                print_info("Reinicie manualmente com: uvicorn app:app --reload")

    print(f"\n{Colors.GREEN}{Colors.BOLD}Rotacao de secrets concluida!{Colors.END}")
    print_info("Verifique se a aplicacao esta funcionando corretamente")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n")
        print_info("Operacao cancelada pelo usuario")
        sys.exit(0)
