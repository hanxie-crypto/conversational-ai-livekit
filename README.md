# 项目介绍
该项目是基于livekit和阿里云服务集成的实时大模型通讯项目，实现了实时聊天效果，可以接入知识库，智能体
![效果图](https://img.alicdn.com/imgextra/i4/O1CN01ZBWe0z1Qz7JRB7IkZ_!!6000000002046-0-tps-3536-1788.jpg)
## 1. 安装及启动live-kit server
以下教程： https://docs.livekit.io/home/self-hosting/local/ 



## 2. 启动agent-quickstart 项目

cd agent-quickstart

export LIVEKIT_URL=http://127.0.0.1:7880

export LIVEKIT_API_KEY=devkey

export LIVEKIT_API_SECRET=secret

export DASHSCOPE_API_KEY=<API_KEY>

export LLM_BASE_URL=<ANY_LLM_API_KEY>

export LLM_API_KEY=<ANY_LLM_API_KEY>


python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python agent.py  dev

## 3.启动前端项目

cd agents-playground

修改 .env.examplate -> .env

npm i

npm run dev


## 补充

Dashscope 的API_KEY也就是百炼基础模型的API_KEY获取地址 https://bailian.console.aliyun.com/#/model-market

LLM_BASE_URL 和  LLM_API_KEY 使用任意兼容 OpenAI 的大模型服务， 
建议使用[AgentCraft](https://agentcraft-docs.serverless-developer.com/) 
这样可以把指令设置，知识库和智能体统一放在一起测试

![AgentCraft获取LLM_BASE_URL 和 LLM_API_KEY](https://img.alicdn.com/imgextra/i4/O1CN01tKfiUo1atkpfLqA82_!!6000000003388-0-tps-3490-1512.jpg)

