#!/usr/bin/env python3
"""
Script de automação para converter arquivos .py, .txt, .toml (e outras extensões)
em PDF, processando recursivamente pastas e subpastas, com opção de ignorar diretórios.
Salva o PDF com o mesmo nome e na mesma pasta do arquivo original.

Uso:
    python converter_pdfs.py [pasta_raiz] [opções]
"""
ll
import sys
import argparse
from pathlib import Path
from datetime import datetime
from fpdf import FPDF

# Configurações padrão (agora são constantes, não modificáveis pelo usuário)
DEFAULT_EXTENSIONS = ['.py', '.txt', '.toml']
DEFAULT_IGNORE_DIRS = {'__pycache__', '.git', 'venv', 'env', '.idea', 'node_modules', '.vscode'}
DEFAULT_MAX_PAGES = 1000
DEFAULT_FONT_SIZE = 9
DEFAULT_LINE_HEIGHT = 5

class TextFileToPDF(FPDF):
    """PDF customizado para arquivos texto"""
    def __init__(self, font_size):
        super().__init__()
        self.font_size = font_size

    def header(self):
        if self.page_no() > 1:
            self.set_font('Courier', size=8)
            self.set_text_color(100, 100, 100)
            self.cell(0, 5, f'Página {self.page_no()}', align='R')
            self.ln(3)

    def footer(self):
        self.set_y(-15)
        self.set_font('Courier', size=7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f'Gerado em {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', align='C')

def convert_to_pdf(input_path, output_path, max_pages, font_size, line_height, verbose=False):
    """
    Converte um arquivo texto para PDF.
    Retorna True se sucesso, False caso contrário.
    """
    try:
        try:
            with open(input_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(input_path, 'r', encoding='latin-1') as f:
                content = f.read()
    except Exception as e:
        if verbose:
            print(f"    ⚠️ Erro de leitura: {e}")
        return False

    pdf = TextFileToPDF(font_size)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_font('Courier', size=font_size)

    # Cabeçalho com nome do arquivo na primeira página
    pdf.set_font('Courier', 'B', size=font_size+2)
    pdf.cell(0, line_height+2, f"Arquivo: {input_path.name}", ln=True)
    pdf.set_font('Courier', size=font_size)
    pdf.ln(4)

    if not content.strip():
        pdf.cell(0, line_height, "[Arquivo vazio]", ln=True)
    else:
        for line in content.splitlines():
            line = line.replace('\x00', '').replace('\r', '')
            try:
                pdf.cell(0, line_height, line, ln=True)
            except:
                safe_line = line.encode('latin-1', errors='replace').decode('latin-1')
                pdf.cell(0, line_height, safe_line, ln=True)

            if pdf.page_no() > max_pages:
                pdf.add_page()
                pdf.set_font('Courier', size=font_size)
                pdf.cell(0, line_height, f"[Arquivo truncado: excedeu {max_pages} páginas]", ln=True)
                break

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pdf.output(str(output_path))
        return True
    except Exception as e:
        if verbose:
            print(f"    ❌ Erro ao salvar PDF: {e}")
        return False

def process_folder(root_path, extensions, ignore_dirs, max_pages, font_size, line_height,
                   dry_run=False, no_overwrite=False, verbose=False):
    """
    Processa recursivamente uma pasta, convertendo todos os arquivos com extensões definidas.
    """
    root = Path(root_path).resolve()
    if not root.is_dir():
        print(f"❌ Erro: '{root_path}' não é um diretório válido.")
        return False

    files_to_convert = []
    for ext in extensions:
        for file_path in root.rglob(f"*{ext}"):
            if any(ignored in file_path.parts for ignored in ignore_dirs):
                if verbose:
                    print(f"⏭️ Ignorando (pasta ignorada): {file_path.relative_to(root)}")
                continue
            if file_path.is_file():
                files_to_convert.append(file_path)

    if not files_to_convert:
        print(f"ℹ️ Nenhum arquivo com extensões {extensions} encontrado em '{root_path}'.")
        return True

    files_to_convert.sort()
    print(f"\n📁 Processando: {root_path}")
    print(f"🔍 Encontrados {len(files_to_convert)} arquivo(s) para converter.\n")

    success = 0
    skipped = 0
    errors = 0

    for src_path in files_to_convert:
        dst_path = src_path.with_suffix('.pdf')
        rel_path = src_path.relative_to(root)

        if no_overwrite and dst_path.exists():
            if verbose:
                print(f"⏩ Pulando (já existe): {rel_path}")
            skipped += 1
            continue

        if dry_run:
            print(f"🔮 [DRY RUN] Converteria: {rel_path} -> {dst_path.name}")
            success += 1
            continue

        print(f"🔄 Convertendo: {rel_path}")
        if convert_to_pdf(src_path, dst_path, max_pages, font_size, line_height, verbose):
            print(f"   ✅ PDF gerado: {dst_path.relative_to(root)}")
            success += 1
        else:
            print(f"   ❌ Falha na conversão: {rel_path}")
            errors += 1

    print("\n" + "="*60)
    print("📊 RESUMO FINAL")
    print(f"   ✅ Sucessos:  {success}")
    if skipped:
        print(f"   ⏩ Pulados:   {skipped} (já existiam PDFs)")
    if errors:
        print(f"   ❌ Erros:     {errors}")
    print(f"   📁 Pasta raiz: {root_path}")
    print("="*60 + "\n")

    return errors == 0

def main():
    parser = argparse.ArgumentParser(
        description="Converte arquivos .py, .txt, .toml (e outras extensões) para PDF, recursivamente.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pasta", nargs="?", default=".",
                        help="Caminho da pasta raiz (padrão: diretório atual)")
    parser.add_argument("--extensions", "-e", nargs="+", default=DEFAULT_EXTENSIONS,
                        help=f"Extensões dos arquivos a processar (padrão: {DEFAULT_EXTENSIONS})")
    parser.add_argument("--ignore-dirs", "-i", nargs="+", default=list(DEFAULT_IGNORE_DIRS),
                        help=f"Pastas a ignorar (padrão: {list(DEFAULT_IGNORE_DIRS)})")
    parser.add_argument("--dry-run", "-d", action="store_true",
                        help="Apenas simula, não gera PDFs")
    parser.add_argument("--no-overwrite", "-n", action="store_true",
                        help="Não sobrescrever PDFs existentes")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Exibe informações detalhadas")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Número máximo de páginas por PDF (padrão: {DEFAULT_MAX_PAGES})")
    parser.add_argument("--font-size", type=int, default=DEFAULT_FONT_SIZE,
                        help=f"Tamanho da fonte em pontos (padrão: {DEFAULT_FONT_SIZE})")
    parser.add_argument("--line-height", type=int, default=DEFAULT_LINE_HEIGHT,
                        help=f"Altura da linha em mm (padrão: {DEFAULT_LINE_HEIGHT})")

    args = parser.parse_args()

    extensions = [ext if ext.startswith('.') else f'.{ext}' for ext in args.extensions]
    ignore_dirs = set(args.ignore_dirs)

    if args.dry_run:
        print("🚀 EXECUÇÃO EM MODO SIMULAÇÃO (DRY-RUN) - NENHUM PDF SERÁ CRIADO\n")

    process_folder(
        root_path=args.pasta,
        extensions=extensions,
        ignore_dirs=ignore_dirs,
        max_pages=args.max_pages,
        font_size=args.font_size,
        line_height=args.line_height,
        dry_run=args.dry_run,
        no_overwrite=args.no_overwrite,
        verbose=args.verbose
    )

if __name__ == "__main__":
    main()