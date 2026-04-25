# Render 部署指南 - 时光杂货店云服务器

本指南详细说明如何将支付宝扫码登录服务从 Railway 迁移到 Render。

## 📋 前提条件

1. **GitHub 账号**（免费）
2. **Render 账号**（免费注册，无需信用卡）
3. **本地代码**：`cloud_server/` 目录下的所有文件

## 🚀 完整部署步骤

### 步骤 1：准备 GitHub 仓库

如果你还没有将代码推送到 GitHub，请按以下步骤操作：

1. **在 GitHub 上创建新仓库**
   - 访问 https://github.com/new
   - 仓库名：`shiguang-cloud-server`（或其他名称）
   - 选择 **Public**（公开，免费）
   - **不要**勾选 "Initialize this repository with a README"
   - 点击 "Create repository"

2. **本地初始化 Git 并推送代码**
   ```bash
   # 进入 cloud_server 目录
   cd cloud_server
   
   # 初始化 Git 仓库
   git init
   
   # 添加所有文件
   git add .
   
   # 提交
   git commit -m "Initial commit: Shiguang QR server"
   
   # 添加远程仓库（替换 YOUR_USERNAME 和 YOUR_REPO_NAME）
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
   
   # 推送代码
   git branch -M main
   git push -u origin main
   ```

### 步骤 2：注册 Render 账号

1. 访问 https://render.com
2. 点击 "Get Started for Free"
3. 使用 **GitHub 账号**登录（推荐，方便后续部署）
4. 完成邮箱验证

### 步骤 3：在 Render 上创建 Web Service

1. **登录 Render Dashboard**
   - 点击右上角 **New +** 按钮
   - 选择 **Web Service**

2. **连接 GitHub 仓库**
   - 点击 "Connect GitHub" 或 "Connect repository"
   - 授权 Render 访问你的 GitHub 账户
   - 选择你的仓库（`shiguang-cloud-server`）

3. **配置 Web Service**
   - **Name**：`shiguang-qr-server`（或你喜欢的名称）
   - **Environment**：选择 **Python 3**
   - **Region**：选择 `Singapore`（离中国较近）或默认区域
   - **Branch**：`main`（默认）

4. **构建和启动命令**
   Render 会自动检测 `requirements.txt` 和 `Procfile`，但请确认以下设置：
   - **Build Command**：`pip install -r requirements.txt`
   - **Start Command**：`gunicorn --bind 0.0.0.0:$PORT app:app`

5. **环境变量设置**
   点击 "Advanced" → "Add Environment Variable"：
   - **Key**：`ADMIN_PASSWORD`
   - **Value**：`shiguang2024`（或你自定义的密码，可选）
   
   > 注意：如果不设置，代码会使用默认值 "shiguang2024"

6. **计划类型**
   - 选择 **Free**（免费计划）
   - 免费额度：每月 750 小时（约 31 天全时运行）

7. **创建 Web Service**
   - 点击 **Create Web Service**
   - Render 将开始自动部署（约 2-5 分钟）

### 步骤 4：获取部署域名

部署完成后，Render 会提供：
- **默认域名**：`https://shiguang-qr-server.onrender.com`
- **自定义域名**：可以在 Settings → Custom Domain 绑定自己的域名

记下你的域名，后续需要配置到本地工具中。

## ⚙️ 配置说明

### 文件结构要求
```
cloud_server/
├── app.py              # Flask 主应用
├── requirements.txt    # Python 依赖
├── Procfile           # 启动命令
├── railway.json       # 可忽略（Render 不使用）
└── RENDER_DEPLOYMENT.md # 本文件
```

### `Procfile` 内容
```
web: gunicorn --bind 0.0.0.0:$PORT app:app
```
- `$PORT`：Render 自动注入的环境变量
- `app:app`：第一个 `app` 是模块名（app.py），第二个是 Flask 应用实例

### `requirements.txt` 内容
```
flask==3.0.0
requests==2.31.0
gunicorn==21.2.0
```

## 🔧 本地工具配置更新

部署成功后，需要更新本地工具中的服务器地址：

### 方法一：设置环境变量（推荐）
在运行本地工具前，设置环境变量：
```bash
# Windows PowerShell
$env:CLOUD_SERVER="https://shiguang-qr-server.onrender.com"

# Windows CMD
set CLOUD_SERVER=https://shiguang-qr-server.onrender.com

# Linux/macOS
export CLOUD_SERVER=https://shiguang-qr-server.onrender.com
```

### 方法二：修改代码默认值
编辑 `shiguang_tool/gui/main.py`，第 75 行：
```python
# 修改前
CLOUD_SERVER_URL = os.environ.get("CLOUD_SERVER", "https://web-production-c5cc5.up.railway.app")

# 修改后
CLOUD_SERVER_URL = os.environ.get("CLOUD_SERVER", "https://shiguang-qr-server.onrender.com")
```

### 方法三：GUI 中手动输入
在工具界面的服务器地址输入框中，直接输入新的 Render 域名。

## 🧪 测试部署是否成功

1. **访问首页**
   ```
   https://shiguang-qr-server.onrender.com/
   ```
   应该能看到二维码页面和"时光杂货店"标题。

2. **测试扫码流程**
   - 点击页面上的二维码或"点击直接跳转登录"
   - 应该能正常跳转到支付宝登录页面
   - 扫码后应返回"登录成功"页面

3. **检查 API 接口**
   ```
   https://shiguang-qr-server.onrender.com/api/health
   ```
   应该返回 JSON 健康状态信息。

4. **查看调试日志**
   ```
   https://shiguang-qr-server.onrender.com/debug
   ```
   可以查看实时日志，方便排查问题。

## ⚠️ Render 免费版限制

1. **休眠机制**
   - 15 分钟无请求后，服务会自动休眠
   - 下次访问时，需要约 10-30 秒唤醒时间
   - 解决方案：可使用免费监控服务（如 UptimeRobot）每 10 分钟访问一次

2. **资源限制**
   - 512 MB RAM
   - 免费 CPU 共享
   - 对于本服务完全足够

3. **每月 750 小时**
   - 约等于 31 天全时运行
   - 如果服务休眠，不计入运行时间

4. **无持久存储**
   - 重启后内存中的数据会丢失
   - 对于 authCode 临时存储无影响

## 🔄 自动唤醒方案（可选）

使用 UptimeRobot 免费监控保持服务活跃：

1. 注册 https://uptimerobot.com
2. 创建新的 Monitor
   - Monitor Type: HTTP(s)
   - URL: 你的 Render 域名
   - Monitoring Interval: 5 minutes
3. 保存后，UptimeRobot 会每 5 分钟访问一次，防止休眠

## 📞 故障排除

### 1. 部署失败
- 检查 `requirements.txt` 格式是否正确
- 查看 Render 的 Build Logs 中的错误信息
- 确保 `app.py` 中没有语法错误

### 2. 服务启动失败
- 检查 `Procfile` 格式
- 确认端口绑定为 `0.0.0.0:$PORT`
- 查看 Runtime Logs 中的错误信息

### 3. 二维码无法显示
- 检查网络连接
- 查看调试日志 `/debug`
- 确认支付宝 API 可访问

### 4. 扫码后无响应
- 检查回调地址是否正确
- 查看后台线程日志
- 确认网络请求无超时

## 📈 监控与维护

1. **Render Dashboard**
   - 查看服务状态
   - 查看日志
   - 监控资源使用

2. **调试页面**
   - `https://你的域名/debug` - 实时日志
   - `https://你的域名/api/health` - 健康检查

3. **GitHub 集成**
   - 每次推送到 `main` 分支会自动触发重新部署
   - 可在 Render 中禁用自动部署

## 🎉 完成迁移

完成以上步骤后，你的云服务器已成功从 Railway 迁移到 Render。所有功能保持不变，且完全免费。

如有问题，请参考：
- Render 官方文档：https://render.com/docs
- 项目 GitHub Issues
- 调试页面日志信息