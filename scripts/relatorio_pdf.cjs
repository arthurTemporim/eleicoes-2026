// Converte um Markdown com diagramas Mermaid em PDF (A4).
// Roda dentro da imagem minlag/mermaid-cli, que já traz Chromium, puppeteer, marked e mermaid;
// use scripts/relatorio_pdf.sh em vez de chamar este arquivo diretamente.
//
// Uso: node relatorio_pdf.cjs <entrada.md> <saida.pdf>
const fs = require("fs");
const path = require("path");
const { marked } = require("marked");
const puppeteer = require("puppeteer");

const [entrada, saida] = process.argv.slice(2);
if (!entrada || !saida) {
  console.error("uso: node relatorio_pdf.cjs <entrada.md> <saida.pdf>");
  process.exit(2);
}

const MERMAID_JS = require.resolve("mermaid/dist/mermaid.min.js");
const CONFIG_PUPPETEER = JSON.parse(fs.readFileSync("/puppeteer-config.json", "utf8"));

const escapar = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// Blocos ```mermaid viram <pre class="mermaid">, renderizados pelo mermaid.js no Chromium
marked.use({
  renderer: {
    code(codigo, linguagem) {
      return linguagem === "mermaid" ? `<pre class="mermaid">${escapar(codigo)}</pre>` : false;
    },
  },
});

const CSS = `
  @page { size: A4; margin: 16mm 14mm 18mm 14mm; }
  body { font-family: "DejaVu Sans", sans-serif; font-size: 9.5pt; line-height: 1.45; color: #1f2328; }
  h1 { font-size: 19pt; border-bottom: 2px solid #1f2328; padding-bottom: 4px; }
  h2 { font-size: 14pt; margin-top: 18px; border-bottom: 1px solid #d0d7de; padding-bottom: 3px; break-after: avoid; }
  h3 { font-size: 11.5pt; margin-top: 14px; break-after: avoid; }
  table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 8.5pt; break-inside: auto; }
  tr { break-inside: avoid; }
  th, td { border: 1px solid #d0d7de; padding: 3px 6px; }
  th { background: #f6f8fa; }
  tbody tr:nth-child(even) { background: #fafbfc; }
  hr { border: none; border-top: 1px solid #d0d7de; margin: 16px 0; }
  code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.5pt; background: #f6f8fa; padding: 0 3px; }
  pre.mermaid { text-align: center; background: none; break-inside: avoid; margin: 10px 0; }
  pre.mermaid svg { max-width: 100%; height: auto; }
`;

(async () => {
  const markdown = fs.readFileSync(entrada, "utf8");
  const titulo = (markdown.match(/^#\s+(.+)$/m) || [, path.basename(entrada)])[1];
  const html = `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
    <title>${escapar(titulo)}</title><style>${CSS}</style></head>
    <body>${marked.parse(markdown)}</body></html>`;

  const navegador = await puppeteer.launch(CONFIG_PUPPETEER);
  try {
    const pagina = await navegador.newPage();
    await pagina.setContent(html, { waitUntil: "load" });
    await pagina.addScriptTag({ path: MERMAID_JS });
    const erros = await pagina.evaluate(async () => {
      mermaid.initialize({ startOnLoad: false, theme: "default", xyChart: { width: 760, height: 380 } });
      const falhas = [];
      for (const el of document.querySelectorAll("pre.mermaid")) {
        try {
          await mermaid.parse(el.textContent);
        } catch (e) {
          falhas.push(`${el.textContent.trim().split("\n").slice(0, 2).join(" | ")}: ${e.message || e}`);
        }
      }
      await mermaid.run({ querySelector: "pre.mermaid", suppressErrors: true });
      return falhas;
    });
    if (erros.length) {
      console.error("Diagramas Mermaid com erro:\n" + erros.join("\n"));
      process.exitCode = 1;
    }
    await pagina.pdf({
      path: saida,
      format: "A4",
      printBackground: true,
      preferCSSPageSize: true,
      displayHeaderFooter: true,
      headerTemplate: "<div></div>",
      footerTemplate: `<div style="font-size:7pt;color:#777;width:100%;padding:0 14mm;display:flex;justify-content:space-between">
        <span>${escapar(titulo)}</span><span><span class="pageNumber"></span>/<span class="totalPages"></span></span></div>`,
    });
    console.log(`PDF gerado: ${saida}`);
  } finally {
    await navegador.close();
  }
})();
