# 可选附录镜像（非验收项；本地开发请用 npm run dev，见 README）
FROM node:20-alpine

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ARG NEXT_PUBLIC_BAIDU_JS_AK=
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE \
    NEXT_PUBLIC_BAIDU_JS_AK=$NEXT_PUBLIC_BAIDU_JS_AK

RUN npm run build
EXPOSE 3000
CMD ["npm", "run", "start"]
