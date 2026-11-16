FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build && chown -R node:node /app
EXPOSE 3000
USER node
CMD ["npm","run","start"]
