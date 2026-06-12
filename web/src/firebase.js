import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";

const firebaseConfig = {
  apiKey: "AIzaSyCjHfOoDx2rK2t6V9pU1aOCaIm19ybE-uY",
  authDomain: "gen-lang-client-0647036765.firebaseapp.com",
  projectId: "gen-lang-client-0647036765",
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
