FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
ARG NEXT_PUBLIC_API_DOCS_URL=http://localhost:8000/docs
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NEXT_PUBLIC_API_DOCS_URL=${NEXT_PUBLIC_API_DOCS_URL}
RUN npm run build
EXPOSE 3000
CMD ["npm","run","start"]
