本目录为项目内置便携 Tesseract（绿色免安装中文 OCR）。

当前已集成：
  tesseract.exe + 依赖 DLL
  tessdata/chi_sim.traineddata  （中文简体）
  tessdata/eng.traineddata      （英文）

程序会自动从此目录加载，无需用户再安装。
打包时 main.spec 会把整个 tesseract/ 打进发行目录。

若中文识别异常，可重新下载语言包：
  curl.exe -L -o tessdata/chi_sim.traineddata https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_sim.traineddata
