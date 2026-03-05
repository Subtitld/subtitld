# GitLab CI/CD para Subtitld

## Visão Geral

Este diretório contém as configurações de CI/CD para build automatizado do Subtitld.

## Estrutura

```
.gitlab/
└── appimage.yml      # Configuração do pipeline de AppImage
```

## Pipeline de AppImage

O job `appimage` gera um pacote AppImage funcional para distribuição em distribuições Linux.

### Variáveis de Ambiente

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `VERSION_NUMBER` | Versão do AppImage | `$CI_COMMIT_TAG` (ou dinâmica) |

### Versionamento

- **Tags**: Usa o nome da tag como versão (ex: `v24.03.05` → versão `24.03.05`)
- **Master**: Usa formato `YY.MM.DD.HHMM-SHA` (ex: `24.03.05.1430-a1b2c3d`)
- **Manual**: Mesmo formato da master

### Quando é executado

| Condição | Comportamento |
|----------|---------------|
| **Tags** | Executa automaticamente |
| **Master** | Executa automaticamente |
| **Outras branches** | Execução manual (allow_failure) |

### Artefatos

Os seguintes arquivos são gerados:

- `*.AppImage` - O pacote AppImage executável
- `*.AppImage.zsync` - Arquivo de sincronização para updates delta

Os artifacts expiram em **30 dias**.

### Jobs Disponíveis

| Job | Descrição | Stage |
|-----|-----------|-------|
| `appimage` | Build do AppImage | build |
| `appimage:release` | Upload para releases (apenas tags) | deploy |

## Configuração no GitLab

### 1. Incluir no pipeline principal

No seu `.gitlab-ci.yml` na raiz:

```yaml
include:
  - local: '.gitlab/appimage.yml'
```

### 2. Variáveis necessárias (Settings → CI/CD → Variables)

Nenhuma variável secreta é necessária para build básico.

Para upload automático em releases:
- O job usa `CI_JOB_TOKEN` automaticamente

### 3. Habilitar releases automáticos

Certifique-se de que as releases são criadas automaticamente ao taggear:

```yaml
# No .gitlab-ci.yml principal
release:
  image: registry.gitlab.com/gitlab-org/release-cli:latest
  rules:
    - if: $CI_COMMIT_TAG
  script:
    - echo "Criando release para $CI_COMMIT_TAG"
  release:
    tag_name: $CI_COMMIT_TAG
    name: "Release $CI_COMMIT_TAG"
    description: "Release automática do Subtitld"
```

## Personalização

### Versão fixa

Para usar versão fixa em vez de dinâmica:

```yaml
appimage:
  variables:
    VERSION_NUMBER: "24.03.05.01"
```

### Upload para servidor próprio

Adicione ao `.gitlab-ci.yml` principal:

```yaml
appimage:upload:
  extends: appimage
  stage: deploy
  script:
    - export SSHPASS=$USER_PASS
    - sshpass -e scp -o stricthostkeychecking=no *.AppImage user@server:/path/
  rules:
    - if: $CI_COMMIT_BRANCH == "master"
  variables:
    USER_PASS:
      value: ""
      description: "Senha para SSH"
```

## Troubleshooting

### Build falha com erro de dependência

Verifique se todas as dependências do sistema estão no `before_script`:

```yaml
before_script:
  - apt-get update
  - apt-get install -y libffms2-5 libmpv1 libglib2.0-0
```

### AppImage não executa

Execute com `--appimage-extract-and-run` para debugging:

```bash
./Subtitld*.AppImage --appimage-extract-and-run --debug
```

### Erro: "Cannot find libmpv"

O `AppImageBuilder.yml` já inclui patches para corrigir este erro. Se persistir:

```yaml
script:
  - appimage-builder --skip-test --recipe AppImageBuilder.yml
  # Extrair para debug
  - ./Subtitld*.AppImage --appimage-extract
  - ls -la squashfs-root/
```

### Tamanho do AppImage

Para reduzir o tamanho, edite o `AppImageBuilder.yml`:

```yaml
AppDir:
  apt:
    include:
      # Apenas dependências essenciais
      - python3
      - libffms2-5
      - libmpv1
```

### Icone não aparece

Verifique se o ícone existe:

```bash
ls -la subtitld/graphics/subtitld.png
```

E atualize o `AppImageBuilder.yml`:

```yaml
AppDir:
  app_info:
    icon: subtitld.png
  files:
    override:
      - usr/share/icons/hicolor/512x512/apps/subtitld.png=subtitld/graphics/subtitld.png
```

## Links úteis

- [Documentação AppImage](https://docs.appimage.org/)
- [appimage-builder](https://appimage-builder.readthedocs.io/)
- [GitLab CI/CD](https://docs.gitlab.com/ee/ci/)
