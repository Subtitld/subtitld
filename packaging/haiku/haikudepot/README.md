# Subtitld on HaikuDepot

HaikuDepot lists what the haikuports repository builds, so Subtitld appears
there once its recipes (`../media-video`, `../dev-python`) are merged into
haikuports. What it shows comes from two places:

- **The package:** the English summary and description (the recipe's
  `SUMMARY` and `DESCRIPTION`) and the icon (the app's, from its resources).
- **HaikuDepot Server** (<https://depot.haiku-os.org>): screenshots,
  categories and the summary and description in other languages. Anyone with
  an account can rate a package; editing these needs permission for the
  package (`PKG_EDITSCREENSHOT`, `PKG_EDITCATEGORIES`, `PKG_EDITLOCALIZATION`),
  which HaikuDepot Server's administrators grant per user and package. Ask on
  the Haiku forum (<https://discuss.haiku-os.org>) or on IRC (#haiku).

Not on Haiku, so not mentioned in any of the texts: transcription and
translation, which come from add-ons, and no add-on is built for Haiku. The
voices to dub with are the built-in online ones (Microsoft Edge, Google).

## Category

Video.

## Screenshots

In `screenshots/`, in this order: `editor.png`, `plain-text.png`,
`vertical-timeline.png`. PNG, 1500 × 766: HaikuDepot Server takes PNG only,
at most 1500 px a side and 2 MB. Taken in Haiku R1/beta6 at 1920 × 1080 (the
whole window fits there; see below) and scaled to 1500 px wide. The video is
Tears of Steel (CC BY 3.0, Blender Foundation).

Subtitld's window is at least 1565 px wide: on narrower screens its right
side is cut off. A narrower layout would want new screenshots.

## Texts

The title is Subtitld in every language. English is the recipe's own; the
others go into HaikuDepot Server's localization for the package. HaikuDepot
has no Vietnamese, the one interface language of Subtitld's left out.

### English (en)

**Summary:** A subtitle and caption editor

**Description:** Subtitld creates and edits subtitles and captions. It has a timeline with the sound's waveform, a video preview, speakers with their own colours, and online synthetic voices (Microsoft Edge, Google) to dub with. It reads and writes the common subtitle formats: SRT, WebVTT, ASS and SSA, TTML, SAMI, SCC and more. Projects (.usfx) keep the video, the subtitles and the place you left them.

### Čeština (cs)

**Summary:** Editor titulků a skrytých titulků

**Description:** Subtitld vytváří a upravuje titulky a skryté titulky. Nabízí časovou osu s průběhem zvuku, náhled videa, mluvčí s vlastními barvami a online syntetické hlasy (Microsoft Edge, Google) pro dabing. Čte a zapisuje běžné formáty titulků: SRT, WebVTT, ASS a SSA, TTML, SAMI, SCC a další. Projekty (.usfx) uchovávají video, titulky i místo, kde jste skončili.

### Deutsch (de)

**Summary:** Ein Editor für Untertitel und Closed Captions

**Description:** Subtitld erstellt und bearbeitet Untertitel und Closed Captions. Es bietet eine Zeitleiste mit der Wellenform des Tons, eine Videovorschau, Sprecher in eigenen Farben und synthetische Online-Stimmen (Microsoft Edge, Google) für die Synchronisation. Es liest und schreibt die gängigen Untertitelformate: SRT, WebVTT, ASS und SSA, TTML, SAMI, SCC und weitere. Projekte (.usfx) bewahren das Video, die Untertitel und die Stelle, an der Sie aufgehört haben.

### Español (es)

**Summary:** Un editor de subtítulos y subtítulos cerrados

**Description:** Subtitld crea y edita subtítulos y subtítulos cerrados. Tiene una línea de tiempo con la forma de onda del sonido, una vista previa del vídeo, hablantes con sus propios colores y voces sintéticas en línea (Microsoft Edge, Google) para doblar. Lee y escribe los formatos de subtítulos habituales: SRT, WebVTT, ASS y SSA, TTML, SAMI, SCC y más. Los proyectos (.usfx) guardan el vídeo, los subtítulos y el punto donde lo dejaste.

### Français (fr)

**Summary:** Un éditeur de sous-titres et de sous-titres codés

**Description:** Subtitld crée et modifie des sous-titres et des sous-titres codés. Il offre une timeline avec la forme d'onde du son, un aperçu vidéo, des locuteurs avec leurs propres couleurs et des voix de synthèse en ligne (Microsoft Edge, Google) pour le doublage. Il lit et écrit les formats de sous-titres courants : SRT, WebVTT, ASS et SSA, TTML, SAMI, SCC et d'autres. Les projets (.usfx) conservent la vidéo, les sous-titres et l'endroit où vous vous étiez arrêté.

### Bahasa Indonesia (id)

**Summary:** Editor subtitle dan teks tertutup

**Description:** Subtitld membuat dan menyunting subtitle serta teks tertutup (closed caption). Aplikasi ini memiliki linimasa dengan bentuk gelombang suara, pratinjau video, pembicara dengan warnanya masing-masing, serta suara sintetis daring (Microsoft Edge, Google) untuk dubbing. Subtitld membaca dan menulis format subtitle umum: SRT, WebVTT, ASS dan SSA, TTML, SAMI, SCC, dan lainnya. Proyek (.usfx) menyimpan video, subtitle, dan posisi terakhir Anda.

### Italiano (it)

**Summary:** Un editor di sottotitoli e didascalie

**Description:** Subtitld crea e modifica sottotitoli e didascalie. Offre una timeline con la forma d'onda del suono, un'anteprima del video, parlanti con i propri colori e voci sintetiche online (Microsoft Edge, Google) per il doppiaggio. Legge e scrive i formati di sottotitoli più comuni: SRT, WebVTT, ASS e SSA, TTML, SAMI, SCC e altri. I progetti (.usfx) conservano il video, i sottotitoli e il punto in cui li ha lasciati.

### 日本語 (ja)

**Summary:** 字幕とクローズドキャプションのエディター

**Description:** Subtitld は字幕とクローズドキャプションを作成・編集します。音声の波形を表示するタイムライン、動画プレビュー、色分けされた話者、吹き替え用のオンライン合成音声（Microsoft Edge、Google）を備えています。SRT、WebVTT、ASS・SSA、TTML、SAMI、SCC など、一般的な字幕形式の読み書きに対応しています。プロジェクト（.usfx）には、動画、字幕、作業を中断した位置が保存されます。

### 한국어 (ko)

**Summary:** 자막 및 폐쇄 자막 편집기

**Description:** Subtitld는 자막과 폐쇄 자막을 만들고 편집합니다. 소리의 파형이 보이는 타임라인, 동영상 미리보기, 고유한 색을 가진 화자, 더빙용 온라인 합성 음성(Microsoft Edge, Google)을 제공합니다. SRT, WebVTT, ASS 및 SSA, TTML, SAMI, SCC 등 일반적인 자막 형식을 읽고 쓸 수 있습니다. 프로젝트(.usfx)에는 동영상, 자막, 작업을 멈춘 위치가 저장됩니다.

### Nederlands (nl)

**Summary:** Een editor voor ondertitels en closed captions

**Description:** Subtitld maakt en bewerkt ondertitels en closed captions. Het heeft een tijdlijn met de golfvorm van het geluid, een videovoorbeeld, sprekers met hun eigen kleuren en synthetische onlinestemmen (Microsoft Edge, Google) om mee na te synchroniseren. Het leest en schrijft de gangbare ondertitelformaten: SRT, WebVTT, ASS en SSA, TTML, SAMI, SCC en meer. Projecten (.usfx) bewaren de video, de ondertitels en de plek waar u was gebleven.

### Polski (pl)

**Summary:** Edytor napisów i napisów dla niesłyszących

**Description:** Subtitld tworzy i edytuje napisy oraz napisy dla niesłyszących. Ma oś czasu z przebiegiem dźwięku, podgląd wideo, mówców w ich własnych kolorach i syntetyczne głosy online (Microsoft Edge, Google) do dubbingu. Odczytuje i zapisuje popularne formaty napisów: SRT, WebVTT, ASS i SSA, TTML, SAMI, SCC i inne. Projekty (.usfx) zachowują wideo, napisy i miejsce, w którym przerwano pracę.

### Português do Brasil (pt-BR)

**Summary:** Um editor de legendas e closed captions

**Description:** O Subtitld cria e edita legendas e closed captions. Tem uma linha do tempo com a forma de onda do som, uma prévia do vídeo, falantes com cores próprias e vozes sintéticas on-line (Microsoft Edge, Google) para dublar. Lê e grava os formatos de legenda mais comuns: SRT, WebVTT, ASS e SSA, TTML, SAMI, SCC e outros. Os projetos (.usfx) guardam o vídeo, as legendas e o ponto onde você parou.

### Русский (ru)

**Summary:** Редактор субтитров и скрытых субтитров

**Description:** Subtitld создаёт и редактирует субтитры и скрытые субтитры. В нём есть таймлайн с волновой формой звука, предпросмотр видео, говорящие со своими цветами и синтетические онлайн-голоса (Microsoft Edge, Google) для дублирования. Он читает и записывает распространённые форматы субтитров: SRT, WebVTT, ASS и SSA, TTML, SAMI, SCC и другие. Проекты (.usfx) сохраняют видео, субтитры и место, где вы остановились.

### Türkçe (tr)

**Summary:** Altyazı ve kapalı altyazı düzenleyicisi

**Description:** Subtitld, altyazıları ve kapalı altyazıları oluşturur ve düzenler. Sesin dalga biçimini gösteren bir zaman çizelgesi, video önizlemesi, kendi renkleriyle konuşmacılar ve dublaj için çevrimiçi sentetik sesler (Microsoft Edge, Google) sunar. Yaygın altyazı biçimlerini okur ve yazar: SRT, WebVTT, ASS ve SSA, TTML, SAMI, SCC ve daha fazlası. Projeler (.usfx) videoyu, altyazıları ve kaldığınız yeri saklar.

### Українська (uk)

**Summary:** Редактор субтитрів і прихованих субтитрів

**Description:** Subtitld створює та редагує субтитри й приховані субтитри. У ньому є шкала часу з хвильовою формою звуку, попередній перегляд відео, мовці з власними кольорами та синтетичні онлайн-голоси (Microsoft Edge, Google) для дублювання. Він читає й записує поширені формати субтитрів: SRT, WebVTT, ASS і SSA, TTML, SAMI, SCC та інші. Проєкти (.usfx) зберігають відео, субтитри й місце, де ви зупинилися.

### 简体中文 (zh-Hans)

**Summary:** 字幕与隐藏式字幕编辑器

**Description:** Subtitld 用于创建和编辑字幕与隐藏式字幕。它提供显示声音波形的时间轴、视频预览、以不同颜色区分的说话人，以及用于配音的在线合成语音（Microsoft Edge、Google）。它可以读写常见的字幕格式：SRT、WebVTT、ASS 和 SSA、TTML、SAMI、SCC 等。项目（.usfx）会保存视频、字幕以及您上次停下的位置。
