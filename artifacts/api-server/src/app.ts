import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();
const isProduction = process.env.NODE_ENV === "production";
const productionDomain = process.env.REPLIT_DOMAINS?.split(",")[0]?.trim();
const productionOrigin =
  process.env.GREENLIGHT_DESK_ORIGIN?.replace(/\/$/, "") ??
  (productionDomain ? `https://${productionDomain}` : undefined);

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(
  cors({
    origin: isProduction
      ? productionOrigin
        ? [productionOrigin]
        : []
      : ["http://localhost:5173", "http://localhost:3000"],
    methods: ["GET", "POST", "OPTIONS"],
    allowedHeaders: ["Content-Type", "Authorization", "X-User-ID"],
    credentials: false,
  }),
);
app.use(express.json({ limit: "8mb" }));
app.use(express.urlencoded({ extended: true, limit: "8mb" }));

app.use("/api", router);

export default app;
