# -*- coding: utf-8 -*-
"""
painel_de_gastos : a unica visao que as ferramentas de infra nao dao.

O Console mostra eventos, o MinIO mostra arquivos, o Qdrant mostra
vetores. Nenhum deles mostra o RESULTADO: para onde foi o dinheiro.

Consome compras-categorizadas num thread de fundo e mantem o estado em
memoria -- de proposito: o lab e sobre streaming, nao sobre banco de
dados. Reiniciar o painel reconstroi tudo a partir do inicio do topico,
o que ja e uma demonstracao de replay.
"""
import json
import threading
from collections import defaultdict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

import comum

registro = comum.log("painel")

app = FastAPI(title="Painel de Gastos", docs_url=None, redoc_url=None)

# Guardado por CHAVE (fatura, linha), nao em lista.
#
# Somar tudo que passa no topico parece natural e esta errado: depois de
# um ./lab.sh replay o mesmo lancamento volta a ser publicado, e o
# painel mostrava 240 compras e R$ 80.370 onde existiam 80 compras e
# R$ 26.790 -- o triplo, por ter processado o historico tres vezes.
#
# Com a chave, reprocessar SUBSTITUI. E a mesma idempotencia que o
# indice no Qdrant precisa. Num pipeline em que o replay e um recurso,
# todo consumidor que acumula estado tem de ser idempotente.
estado = {"compras": {}}
trava = threading.Lock()


def _agregar():
    """Recalcula os totais a partir das compras unicas."""
    por_categoria, por_origem = defaultdict(float), defaultdict(int)
    por_fatura = defaultdict(lambda: {"linhas": 0, "total": 0.0, "banco": "?"})
    total = 0.0

    for c in estado["compras"].values():
        por_categoria[c["categoria"]] += c["valor"]
        por_origem[c["origem_da_categoria"]] += 1
        total += c["valor"]
        f = por_fatura[c["fatura_id"]]
        f["linhas"] += 1
        f["total"] += c["valor"]
        f["banco"] = c.get("banco", "?")

    return por_categoria, por_origem, por_fatura, round(total, 2)

CORES = [
    "#2563eb", "#16a34a", "#ea580c", "#9333ea", "#0891b2",
    "#ca8a04", "#dc2626", "#4f46e5", "#059669", "#db2777", "#64748b",
]


def consumir():
    consumidor = comum.esperar(
        "kafka",
        lambda: comum.consumidor_kafka("grupo-painel", comum.TOPICO_CATEGORIZADAS, do_inicio=True),
    )
    registro.info("painel consumindo %s", comum.TOPICO_CATEGORIZADAS)

    while True:
        msg = consumidor.poll(1.0)
        if msg is None or msg.error():
            continue
        try:
            c = json.loads(msg.value())
        except Exception:
            continue
        if c.get("tipo") == "credito":
            continue

        with trava:
            chave = (c.get("fatura_id"), c.get("linha"))
            estado["compras"][chave] = c


@app.on_event("startup")
def iniciar():
    threading.Thread(target=consumir, daemon=True).start()


@app.get("/dados")
def dados():
    with trava:
        por_categoria, por_origem, por_fatura, total = _agregar()
        categorias = sorted(por_categoria.items(), key=lambda x: -x[1])
        maiores = sorted(estado["compras"].values(), key=lambda c: -c["valor"])[:12]
        return {
            "total": total,
            "quantidade": len(estado["compras"]),
            "categorias": [{"nome": n, "valor": round(v, 2)} for n, v in categorias],
            "origem": dict(por_origem),
            "faturas": [
                {"id": k, "banco": v["banco"], "linhas": v["linhas"], "total": round(v["total"], 2)}
                for k, v in por_fatura.items()
            ],
            "maiores": [
                {
                    "data": c["data"],
                    "descricao": c["descricao"],
                    "valor": c["valor"],
                    "categoria": c["categoria"],
                    "origem": c["origem_da_categoria"],
                }
                for c in maiores
            ],
        }


PAGINA = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel de Gastos</title>
<style>
 :root{--fundo:#0f172a;--cartao:#1e293b;--borda:#334155;--texto:#e2e8f0;--fraco:#94a3b8}
 *{box-sizing:border-box}
 body{margin:0;padding:24px;background:var(--fundo);color:var(--texto);
      font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}
 h1{font-size:19px;margin:0 0 4px}
 .sub{color:var(--fraco);font-size:12px;margin-bottom:20px}
 .grade{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
 .cartao{background:var(--cartao);border:1px solid var(--borda);border-radius:10px;padding:16px}
 .cartao h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;
            color:var(--fraco);margin:0 0 14px;font-weight:600}
 .numero{font-size:30px;font-weight:650;letter-spacing:-.02em}
 .linha{display:flex;align-items:center;gap:10px;margin-bottom:9px}
 .rotulo{width:96px;font-size:12px;flex-shrink:0}
 .trilha{flex:1;height:9px;background:#0b1220;border-radius:5px;overflow:hidden}
 .barra{height:100%;border-radius:5px;transition:width .5s}
 .valor{width:104px;text-align:right;font-variant-numeric:tabular-nums;font-size:12px}
 table{width:100%;border-collapse:collapse;font-size:12.5px}
 td{padding:5px 0;border-bottom:1px solid #27354a}
 td:last-child{text-align:right;font-variant-numeric:tabular-nums}
 .etiqueta{font-size:10px;padding:2px 7px;border-radius:20px;border:1px solid var(--borda);color:var(--fraco)}
 .ia{color:#c4b5fd;border-color:#6d28d9}
 .vazio{color:var(--fraco);padding:28px 0;text-align:center}
</style></head><body>
<h1>Painel de Gastos</h1>
<div class="sub">lendo <code>compras-categorizadas</code> em tempo real — atualiza a cada 2 s</div>
<div class="grade">
  <div class="cartao"><h2>Total analisado</h2>
    <div class="numero" id="total">—</div>
    <div class="sub" id="qtd" style="margin:6px 0 0"></div></div>
  <div class="cartao"><h2>Como foi categorizado</h2><div id="origem"></div></div>
  <div class="cartao"><h2>Faturas processadas</h2><div id="faturas"></div></div>
</div>
<div class="grade" style="margin-top:16px">
  <div class="cartao"><h2>Gastos por categoria</h2><div id="categorias"></div></div>
  <div class="cartao"><h2>Maiores gastos</h2><div id="maiores"></div></div>
</div>
<script>
const CORES=["#2563eb","#16a34a","#ea580c","#9333ea","#0891b2","#ca8a04","#dc2626","#4f46e5","#059669","#db2777","#64748b"];
const brl=v=>v.toLocaleString("pt-BR",{style:"currency",currency:"BRL"});

function barras(alvo,itens,total){
  if(!itens.length){alvo.innerHTML='<div class="vazio">aguardando eventos…</div>';return}
  alvo.innerHTML=itens.map((i,n)=>{
    const pct=total?(i.valor/total*100):0;
    return `<div class="linha"><div class="rotulo">${i.nome}</div>
      <div class="trilha"><div class="barra" style="width:${pct}%;background:${CORES[n%CORES.length]}"></div></div>
      <div class="valor">${brl(i.valor)}</div></div>`}).join("");
}

async function atualizar(){
  let d; try{ d=await (await fetch("/dados")).json() }catch(e){ return }
  document.getElementById("total").textContent=brl(d.total);
  document.getElementById("qtd").textContent=`${d.quantidade} compras categorizadas`;

  const o=d.origem||{}, somaO=(o.catalogo||0)+(o.ia||0)+(o.regra||0);
  document.getElementById("origem").innerHTML = somaO? [
    {nome:"catálogo",valor:o.catalogo||0},{nome:"IA (LLM)",valor:o.ia||0},{nome:"regra",valor:o.regra||0}
  ].map((i,n)=>{const pct=somaO?i.valor/somaO*100:0;
     return `<div class="linha"><div class="rotulo">${i.nome}</div>
       <div class="trilha"><div class="barra" style="width:${pct}%;background:${CORES[n]}"></div></div>
       <div class="valor">${i.valor}</div></div>`}).join("")
   : '<div class="vazio">aguardando eventos…</div>';

  document.getElementById("faturas").innerHTML = d.faturas.length
    ? `<table>${d.faturas.map(f=>`<tr><td><code>${f.id}</code></td><td>${f.banco}</td>
        <td>${f.linhas} linhas</td><td>${brl(f.total)}</td></tr>`).join("")}</table>`
    : '<div class="vazio">nenhuma fatura ainda</div>';

  barras(document.getElementById("categorias"),d.categorias,d.total);

  document.getElementById("maiores").innerHTML = d.maiores.length
    ? `<table>${d.maiores.map(c=>`<tr><td>${c.data}</td>
        <td>${c.descricao.slice(0,30)}<br><span class="etiqueta ${c.origem==='ia'?'ia':''}">${c.categoria} · ${c.origem}</span></td>
        <td>${brl(c.valor)}</td></tr>`).join("")}</table>`
    : '<div class="vazio">aguardando eventos…</div>';
}
atualizar(); setInterval(atualizar,2000);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def pagina():
    return PAGINA
