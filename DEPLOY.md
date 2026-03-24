# 时光杂货店 - 永久二维码云服务部署指南

## 项目结构
```
cloud_server/
├── app.py           # 主服务器
├── requirements.txt # Python依赖
├── Procfile         # 启动命令
└── railway.json     # Railway配置
```

## 部署到 Railway（免费，推荐）

### 步骤1：注册 Railway
1. 打开 https://railway.app
2. 用 GitHub 或 Google 账号登录

### 步骤2：创建新项目
1. 点击 "New Project"
2. 选择 "Deploy from GitHub repo"
3. 如果没有Git仓库，选 "Empty Project"

### 步骤3：上传代码
**方法A - 通过GitHub（推荐）：**
```bash
cd cloud_server
git init
git add .
git commit -m "init"
git remote add origin https://github.com/你的用户名/shiguang-qr.git
git push -u origin main
```
然后在Railway里选这个repo

**方法B - 直接在Railway里创建：**
1. 选择 "Empty Project"
2. 点 "New Service" -> "Python"
3. 上传文件，或者连接GitHub

### 步骤4：设置环境变量（可选）
在 Railway 的 Service Settings -> Variables 中添加：
- `ADMIN_PASSWORD` = 你的管理密码（默认: shiguang2024）

### 步骤5：完成
Railway 会自动部署，部署完成后会给你一个公网URL，类似：
`https://shiguang-qr-production.up.railway.app`

这个URL就是你的永久二维码地址！

## 使用方法

部署成功后：
- 首页: `https://你的域名/` → 看到二维码
- 扫码: `https://你的域名/login` → 自动获取token并跳转
- 管理后台: `https://你的域名/admin?pw=shiguang2024`
- 二维码图片: `https://你的域名/qr`

## 管理后台功能
- 在线更新 x-game-token-pcweb
- 修改站点标题
- 修改管理密码
- 查看扫码统计
- 查看已收集账号
- 清空账号记录

## 费用
Railway 免费额度：每月 $5 或 500 小时运行时间
对于这个小程序来说完全够用
