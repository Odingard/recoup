import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";

// Public Firebase web-app config (not a secret); override per environment via VITE_FIREBASE_*.
const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY || "AIzaSyDYaDZn_uKkNxEK7pHeAhVV1u9JTHkYIMk",
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN || "gen-lang-client-0647036765.firebaseapp.com",
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID || "gen-lang-client-0647036765",
  appId: import.meta.env.VITE_FIREBASE_APP_ID || "1:921318314706:web:896a13bd6929c6f51b930b",
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
