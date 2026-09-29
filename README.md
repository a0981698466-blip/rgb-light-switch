# 💡 RGB 燈光開關

一鍵關閉（或恢復）電腦上所有 RGB 燈光：主機板、記憶體、顯示卡、SSD、散熱風扇、燈條、鍵盤、滑鼠……  
不用分別打開 Armoury Crate、Synapse、G HUB、iCUE、Mystic Light 等原廠軟體。

## 📥 下載

**[按這裡下載 RGBLightSwitch.exe](https://github.com/a0981698466-blip/rgb-light-switch/releases/latest/download/RGBLightSwitch.exe)**

不用安裝，下載後雙擊就能用（Windows 10 / 11，64 位元）。

## 使用方式

1. 雙擊 `RGBLightSwitch.exe`，Windows 詢問是否允許變更時按「**是**」（控制主機板和記憶體燈光需要系統管理員權限）。
2. 第一次開啟會自動下載免費開源的 [OpenRGB](https://openrgb.org) 控制核心（約 20MB，只需一次，需要網路）。
3. 程式會自動偵測燈光裝置，勾選想關的裝置後按「**🌑 關閉勾選的燈光**」。
4. 想開回來時按「**💡 恢復勾選的燈光**」，可以在「恢復成」選單中選擇彩虹、白光、自選顏色，或交還給原廠軟體。

### 燈關了又自己亮起來？

原廠燈光軟體（ASUS Aura、Razer Chroma、Logitech G HUB 等）會把燈改回去。  
請按「**暫停原廠燈光軟體**」，只會停止燈光相關程式，重新開機後會自動恢復。

### 記憶體 / 部分主機板的燈沒有出現在清單？

按「**安裝 PawnIO 驅動**」（OpenRGB 官方建議的驅動），安裝後重新開機即可。

### 還是不行？

到「**電腦配置**」分頁按「**複製診斷報告**」，把內容傳給幫忙的人，就能知道電腦裡有哪些燈光裝置和軟體。

## ⚠️ 開啟時出現「Windows 已保護您的電腦」

這個程式沒有購買數位簽章，所以 Windows 會提醒。按「**其他資訊**」→「**仍要執行**」即可。  
原始程式碼全部公開在本頁（`rgb_share.pyw`），可以自行檢查或自行打包。

## 支援的裝置

實際支援範圍以 OpenRGB 為準，涵蓋 ASUS、MSI、Gigabyte、ASRock 主機板，多數 RGB 記憶體與顯示卡，以及 Razer、Logitech、Corsair、SteelSeries、HyperX 等周邊。  
完整清單：<https://openrgb.org/devices.html>

## 自行打包

```
pip install openrgb-python pyinstaller
pyinstaller --onefile --windowed --uac-admin --icon lightbulb.ico --add-data "lightbulb.ico;." --name RGBLightSwitch rgb_share.pyw
```

## 授權

GPL-3.0。本程式使用 [OpenRGB](https://gitlab.com/CalcProgrammer1/OpenRGB)（GPL-2.0，執行時下載）與 [openrgb-python](https://github.com/jath03/openrgb-python)。
