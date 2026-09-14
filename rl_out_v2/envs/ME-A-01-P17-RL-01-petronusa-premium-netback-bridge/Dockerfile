FROM python:3.12-slim
RUN pip install --no-cache-dir \
    python-docx \
    python-pptx \
    openpyxl \
    pandas \
    markdown
WORKDIR /task
RUN mkdir -p /task/reference /task/output
