# Uma imagem, varios papeis: todos os microsservicos deste lab saem
# daqui e se diferenciam pelo "command" no docker-compose.yml. Assim o
# aluno espera UM build em vez de cinco.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/modelos

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Baixa o modelo de embedding JA no build. Sem isso, o primeiro evento
# da aula dispararia um download de ~220 MB e pareceria travamento.
RUN python -c "\
from fastembed import TextEmbedding; \
TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'); \
print('modelo de embedding embutido na imagem')"

COPY . .

CMD ["python", "-c", "print('informe um command no docker-compose.yml')"]
