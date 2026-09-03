# **AutoShorts-MCP：AI 驅動短影音自動化剪輯系統 (v1.0)**

## **軟體需求規格與專案開發計畫書 (Software Requirements Specification & Project Plan)**

## **1\. 專案概述 (Project Overview)**

### **1.1 背景與問題陳述**

現代短影音（YouTube Shorts、Instagram Reels、TikTok）需求龐大，但傳統剪輯流程面臨以下門檻：

1. **學習成本高**：非專業使用者缺乏剪輯軟體（如 Premiere Pro、DaVinci Resolve）的時間軸操作、轉場設定與關鍵影格概念。  
2. **耗時繁瑣**：素材挑選、音樂節拍對點（卡點）、畫面直式自適應（16:9 轉 9:16）等重複性工序耗費大量精力。  
3. **運算資源瓶頸**：自動化剪輯若採用容器化封裝或單次巨型 Filtergraph 渲染，極易引發記憶體溢位（Out-Of-Memory, OOM），且無法直接善用主機端顯示卡之硬體編碼加速。

### **1.2 專案目標**

打造一套基於 **Model Context Protocol (MCP)** 標準的影音剪輯系統，由大型語言模型（LLM，以 Gemini 3.8 Flash 為主要決策大腦）扮演「導演與剪輯師」，藉由結構化工具呼叫（Tool Use）驅動底層影音處理管線。使用者僅需提供：

* 素材資料夾（影片檔、相片檔）  
* 剪輯風格描述（Prompt）  
* 音樂來源（本機音訊或 YouTube 網址）

系統自動完成視聽分析、卡點編排，並輸出高品質 1080x1920 (9:16) 短影音或剪輯軟體草稿工程檔。

### **1.3 專案範疇 (Scope)**

* **包含範疇 (In-Scope)**：  
  * 音訊自動下載、轉碼與節拍點偵測（BPM、Beat Timestamps、Downbeats）。  
  * 媒體素材規格探測（FFprobe）與輕量化低解析度抽樣（防止 OOM）。  
  * LLM 語意分析生成標準剪輯決定表（Edit Decision List, EDL）。  
  * **雙軌輸出架構**：  
    * **軌道 A（極速草稿）**：輸出 CapCut / 剪映本機工程檔（draft\_content.json），提供無損即時微調。  
    * **軌道 B（原生直出）**：分段處理（Chunked Pipeline）結合本機 GPU 硬體編碼（AMD AMF / NVENC），直接產出最終 MP4。  
* **排除範疇 (Out-of-Scope)**：  
  * 雲端多租戶 SaaS 平台維運。  
  * 複雜的 3D 特效合成與深度神經網路面部重構。

## **2\. 系統架構與設計模式 (System Architecture & Patterns)**

本系統採 **MCP 代理人模式 (MCP Agent Architecture)** 搭配 **分離式雙軌渲染管線**：

                    ┌────────────────────────────────────────┐  
                    │               使用者介面                │  
                    │   (CLI 互動模式 / Web 輕量操控面板)    │  
                    └──────────────────┬─────────────────────┘  
                                       │ 使用者輸入 (Prompt, 音樂, 素材目錄)  
                                       ▼  
                    ┌────────────────────────────────────────┐  
                    │         LLM 導演 Agent (Gemini)         │  
                    │  \* 負責語意決策、片段篩選、分鏡編排    │  
                    └──────────────────┬─────────────────────┘  
                                       │ MCP 協定 (stdio / JSON-RPC 2.0)  
                                       ▼  
┌─────────────────────────────────────────────────────────────────────────────────┐  
│                           AutoShorts-MCP Server 核心                            │  
├─────────────────────────────────────────────────────────────────────────────────┤  
│ \[Tool 1: yt-dlp\]        │ 下載最佳音訊串流並轉換為標準 WAV (44.1kHz, 16-bit)   │  
│ \[Tool 2: Librosa\]       │ 偵測動態 Onset、計算 Tempo (BPM) 與 Beat 切點時間戳   │  
│ \[Tool 3: FFprobe/Scale\] │ 降解析度抽樣（360p 縮圖）供 Vision 模型辨識，抑制 RAM │  
│ \[Tool 4: EDL Engine\]    │ 驗證並建立標準化剪輯決定表 (JSON Schema 規範)          │  
└──────────────────────┬───────────────────────────────────┬──────────────────────┘  
                       │                                   │  
         \[軌道 A: 草稿輸出模式\]              \[軌道 B: 本機原生渲染模式\]  
                       │                                   │  
                       ▼                                   ▼  
        ┌─────────────────────────────┐     ┌─────────────────────────────┐  
        │  CapCut Draft Generator     │     │  Chunked Native Pipeline    │  
        │  \* 寫入 draft\_content.json  │     │  \* 逐片預裁切 (Isolated)    │  
        │  \* 自動建立本機專案目錄     │     │  \* Concat Demuxer 無損拼接  │  
        │  \* 零轉碼運算、零 OOM 風險  │     │  \* AMD AMF / NVENC 硬體加速 │  
        └──────────────┬──────────────┘     └──────────────┬──────────────┘  
                       │                                   │  
                       ▼                                   ▼  
        \[ CapCut 軟體內開啟微調匯出 \]           \[ 直接生成最終 1080x1920 MP4 \]

### **2.1 記憶體防爆機制 (OOM Prevention Paradigm)**

為徹底消除 Docker 或巨型 FFmpeg 命令引發的記憶體暴增，架構實施三層隔離：

1. **抽樣降規隔離**：視訊分析階段不抽取高解析度原幀，透過 FFmpeg 的 scale=360:-1 限制單張縮圖尺寸於數十 KB 內，單一素材取樣上限為 12 張。  
2. **處理時間分段 (Chunking)**：渲染引擎禁止將多路 1080p/4K 視訊同時掛載至單一記憶體圖形中。每個片段單獨啟動子進程執行裁切與 9:16 自適應，寫入硬碟暫存後釋放記憶體。  
3. **無損拼接 (Concat Demuxing)**：所有子片段標準化（1080x1920, 30fps, 統一編碼）後，使用 FFmpeg 的 \-f concat 串接清單，僅進行封裝層（Demuxer）拷貝，記憶體消耗恆定在 50MB 以下。

## **3\. 執行環境與硬體加速規格 (Environment & Hardware Specs)**

系統預設運行於本機作業系統（推薦 Windows 11 或 Ubuntu LTS），以完整調用原生驅動程式與硬體解碼/編碼單元。

### **3.1 建議硬體與加速對應表**

| 硬體組件 | 建議規格基準 | 系統運用與最佳化策略 |
| :---- | :---- | :---- |
| **中央處理器 (CPU)** | 6 核心 / 12 執行緒以上 (如 AMD Ryzen 5 7600\) | 負責多執行緒音訊頻譜分析 (Librosa) 與 JSON 資料組裝。 |
| **系統記憶體 (RAM)** | 16GB DDR4/DDR5 以上 | 提供分段預處理暫存；Chunked 架構下峰值記憶體占用率 ![][image1]。 |
| **顯示卡 (GPU)** | 獨立顯示卡 (如 AMD Radeon RX 系列 或 NVIDIA RTX) | **啟用原生硬體加速**： • AMD 平台：指定 \-c:v h264\_amf 或 hevc\_amf • NVIDIA 平台：指定 \-c:v h264\_nvenc |
| **儲存裝置 (SSD)** | PCIe NVMe SSD | 提供大量中間切片之高頻率讀寫，避免 I/O 等待造成管線延遲。 |

### **3.2 軟體依賴規格**

* **Runtime**: Python 3.11+  
* **FFmpeg**: FFmpeg 6.1+ 或 7.x（需編譯支援 amf 或 nvenc）  
* **Python 核心套件依賴**:  
  * mcp\>=1.2.0：標準 Model Context Protocol 通訊協定  
  * librosa\>=0.10.2：音訊訊號處理與節拍萃取  
  * yt-dlp\>=2024.08.06：影音串流擷取  
  * google-genai\>=0.1.0：Gemini 2.5 API 互動  
  * pydantic\>=2.7.0：資料模型與 Schema 驗證

## **4\. MCP 工具介面規格 (MCP Tool Specifications)**

MCP Server 需實作並向 LLM Client 暴露下列 5 個原子化核心工具：

### **4.1 download\_music**

* **功能描述**：解析 YouTube URL 或驗證本機音訊路徑，轉碼為標準未壓縮 WAV 檔。  
* **輸入參數 (Input Schema)**：  
  {  
    "type": "object",  
    "properties": {  
      "source": { "type": "string", "description": "YouTube 網址或本機音訊檔案絕對路徑" },  
      "output\_name": { "type": "string", "description": "儲存檔名 (不含副檔名)" }  
    },  
    "required": \["source", "output\_name"\]  
  }

* **輸出結果**：file\_path: str（絕對路徑）、duration: float（總秒數）。

### **4.2 analyze\_audio\_beats**

* **功能描述**：使用 Librosa 分析音訊 Onset Strength Envelope，計算整體節奏（BPM）與所有重音/節拍點。  
* **輸入參數 (Input Schema)**：  
  {  
    "type": "object",  
    "properties": {  
      "audio\_path": { "type": "string", "description": "目標音訊 WAV 檔案絕對路徑" },  
      "max\_duration": { "type": "number", "default": 60.0, "description": "分析長度上限 (短影音預設 60 秒)" }  
    },  
    "required": \["audio\_path"\]  
  }

* **輸出結果**：  
  {  
    "bpm": 128.0,  
    "total\_beats": 120,  
    "beat\_timestamps": \[0.46, 0.93, 1.40, 1.87, 2.34, 2.81\],  
    "downbeats": \[0.46, 2.34, 4.22\]  
  }

### **4.3 probe\_and\_sample\_media**

* **功能描述**：探測目錄內所有影片與照片之技術屬性，並強制降解析度抽樣關鍵幀（縮圖）。  
* **輸入參數 (Input Schema)**：  
  {  
    "type": "object",  
    "properties": {  
      "input\_directory": { "type": "string", "description": "放置使用者素材的資料夾路徑" },  
      "sample\_max\_frames": { "type": "integer", "default": 8, "description": "單支影片均勻抽樣幀數上限" }  
    },  
    "required": \["input\_directory"\]  
  }

* **輸出結果**：包含每個檔案之時長、原生寬高比、FPS、編碼格式，以及抽取後的 360p JPEG 縮圖路徑清單。

### **4.4 export\_capcut\_draft (軌道 A)**

* **功能描述**：將 LLM 產生的剪輯決定表轉換為 CapCut / 剪映本機工程目錄。  
* **輸入參數 (Input Schema)**：  
  {  
    "type": "object",  
    "properties": {  
      "project\_name": { "type": "string" },  
      "edl": { "$ref": "\#/definitions/EditDecisionList" }  
    },  
    "required": \["project\_name", "edl"\]  
  }

* **輸出結果**：draft\_folder\_path: str（本機草稿資料夾路徑）、status: str。

### **4.5 render\_chunked\_video (軌道 B)**

* **功能描述**：啟動本機原生多行程分段裁切、濾鏡自適應與硬體加速拼接。  
* **輸入參數 (Input Schema)**：  
  {  
    "type": "object",  
    "properties": {  
      "edl": { "$ref": "\#/definitions/EditDecisionList" },  
      "hw\_accel": { "type": "string", "enum": \["amf", "nvenc", "cpu"\], "default": "amf" },  
      "output\_file": { "type": "string" }  
    },  
    "required": \["edl", "output\_file"\]  
  }

* **輸出結果**：output\_path: str、render\_time\_seconds: float。

## **5\. 核心演算法與資料模型 (Data Models & Core Logic)**

### **5.1 剪輯決定表 (EDL) 資料模型定義**

LLM 透過 System Prompt 約束，必須輸出符合下列 Pydantic 規範的 JSON：

{  
  "$schema": "\[http://json-schema.org/draft-07/schema\#\](http://json-schema.org/draft-07/schema\#)",  
  "title": "EditDecisionList",  
  "type": "object",  
  "properties": {  
    "project\_name": { "type": "string" },  
    "canvas": {  
      "type": "object",  
      "properties": {  
        "width": { "type": "integer", "default": 1080 },  
        "height": { "type": "integer", "default": 1920 },  
        "fps": { "type": "integer", "default": 30 }  
      },  
      "required": \["width", "height", "fps"\]  
    },  
    "background\_audio": {  
      "type": "object",  
      "properties": {  
        "file\_path": { "type": "string" },  
        "start\_offset": { "type": "number", "default": 0.0 },  
        "volume": { "type": "number", "default": 1.0 },  
        "fade\_out": { "type": "number", "default": 1.5 }  
      },  
      "required": \["file\_path"\]  
    },  
    "timeline": {  
      "type": "array",  
      "items": {  
        "type": "object",  
        "properties": {  
          "clip\_id": { "type": "integer" },  
          "source\_file": { "type": "string" },  
          "source\_type": { "type": "string", "enum": \["video", "image"\] },  
          "source\_start": { "type": "number", "description": "素材截取起始秒數 (圖片填 0)" },  
          "duration": { "type": "number", "description": "在時間軸上佔用的持續秒數 (必須對齊 Beat)" },  
          "reframe\_mode": {   
            "type": "string",   
            "enum": \["blur\_padding", "center\_crop", "ken\_burns\_zoom"\]   
          },  
          "transition": {  
            "type": "string",  
            "enum": \["cut", "fade\_black", "crossfade"\],  
            "default": "cut"  
          }  
        },  
        "required": \["clip\_id", "source\_file", "source\_type", "source\_start", "duration", "reframe\_mode"\]  
      }  
    }  
  },  
  "required": \["project\_name", "canvas", "background\_audio", "timeline"\]  
}

### **5.2 節拍吸附演算法 (Beat Snapping Logic)**

LLM 針對每個鏡頭估算所需長度後，演算法強制進行「網格化吸附」：

![][image2]確保每個鏡頭的交界點嚴格對齊音訊能量峰值（Onset）。

### **5.3 直式 9:16 自適應濾鏡鏈 (Filtergraph Formula)**

橫向影片（16:9）轉為直向（9:16）動態模糊背景之 FFmpeg 標準指令：

\[0:v\]split=2\[fg\]\[bg\];  
\[bg\]scale=1080:1920:force\_original\_aspect\_ratio=increase,crop=1080:1920,boxblur=luma\_radius=25:luma\_power=3\[blurred\];  
\[fg\]scale=1080:1920:force\_original\_aspect\_ratio=decrease\[foreground\];  
\[blurred\]\[foreground\]overlay=(W-w)/2:(H-h)/2\[outv\]

## **6\. 技術風險管理與因應對策 (Risk Management)**

| 潛在風險 / 故障點 | 嚴重度 | 根本原因分析 | 專案緩解與防禦措施 |
| :---- | :---- | :---- | :---- |
| **音畫不同步 (AV Desync)** | 高 | FFmpeg 使用 Keyframe 跳躍截取（-ss 放在 \-i 之前）導致切點飄移。 | **MANDATORY**: 預處理必須採用 Frame-accurate 切片指令：-i input.mp4 \-vf "trim=start=X:end=Y,setpts=PTS-STARTPTS"，嚴禁粗糙關鍵影格搜尋。 |
| **記憶體溢位 (OOM)** | 高 | 單一進程載入巨型 4K 素材或長時間 Filtergraph。 | **MANDATORY**: 嚴格執行 Chunk 獨立進程預裁切，每個片段輸出完畢即銷毀物件，最後僅呼叫 Concat Demuxer。 |
| **檔案路徑解析失敗** | 中 | Windows 反斜線 \\ 與空格在命令列中逃逸失效。 | 程式碼內全面使用 pathlib.Path.resolve().as\_posix() 強制標準化為正斜線 /，並在 Shell 命令中外加引號保護。 |
| **YouTube 封鎖下載** | 中 | yt-dlp 遇到 Bot 驗證或格式變更。 | 內建降級保護：若 download\_music 失敗，提示使用者直接放置 .mp3/.wav 檔案至 inputs/ 目錄。 |

## **7\. 專案開發里程碑 (Milestones & Roadmap)**

\[ W1: 核心工具鏈建置 \] ──\> \[ W2: MCP 服務與 Agent 串接 \] ──\> \[ W3: 雙軌輸出實作 \] ──\> \[ W4: 整合驗收與最佳化 \]

### **Milestone 1：底層影音核心模組 (W1)**

* \[ \] 封裝 Librosa 節拍分析模組，輸出精確時間戳 JSON。  
* \[ \] 封裝 yt-dlp 下載與轉碼流程。  
* \[ \] 建立具備 9:16 自適應與動態模糊之獨立片段裁切腳本（驗證硬體加速編碼參數）。

### **Milestone 2：MCP Server 與 Agent 協定 (W2)**

* \[ \] 基於 Python mcp SDK 實作標準 MCP 介面。  
* \[ \] 設計 System Prompt，使 Gemini 2.5 能正確讀取素材目錄縮圖並輸出符合 Schema 的 EDL。  
* \[ \] 實作節拍吸附後處理器（Beat Snapper）。

### **Milestone 3：雙軌輸出引擎實作 (W3)**

* \[ \] **實作軌道 A**：撰寫 CapCut/剪映 draft\_content.json 自動組裝器，測試匯入時間軸精準度。  
* \[ \] **實作軌道 B**：實作 Chunked Pipeline 批次管理器與 Concat Demuxer 串接器。  
* \[ \] 整合硬體加速自動偵測器（自動偵測 AMF / NVENC / QSV）。

### **Milestone 4：系統整合驗收 (W4)**

* \[ \] 以 10 支 4K 橫式生活影片與 1 首 128 BPM 音樂進行端到端全自動測試。  
* \[ \] 驗證長時間執行之記憶體穩定度（RAM 峰值需控制於 3GB 以內）。  
* \[ \] 撰寫一鍵啟動腳本與簡易使用者指引。

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEUAAAAWCAYAAACWl1FwAAAAzElEQVR4Xu2WMQoCMRBFs4iVjWARJIQkld026TyAzYK1tV7DwhPYeQA9gK3gBWzsBSsrwVv4B60GLyDzHzzY7Ew1hJ9xjhBCCCH/RCmlTSmd4A3fM123Rg+D6OAFTuWsG8zgvR9gCCv4hAddN0cIYYRBvOAWjnXdCk3O+QzvcFFr7esGc2AQQ9yIR4xx7ixnhgYvygSDOX4zZKnrppEMgTu4lmzRdfNItkjGpM9u0uJXo3ssI7uK7ClX2MlZN5hFcgc3Zw83ukYIIeQ3b11nIQehACnRAAAAAElFTkSuQmCC>

[image2]: <data:image/png;base64,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>