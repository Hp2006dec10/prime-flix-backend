"use client";

import React, { createContext, useContext, useEffect, useState } from "react";

export interface User {
  id: number;
  full_name: string;
  email: string;
  is_verified: boolean;
  is_active: boolean;
  created_at: string;
}

interface AuthContextType {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  loading: boolean;
  login: (accessToken: string, refreshToken: string, user: User) => void;
  logout: () => void;
  updateUser: (user: User) => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("primeflix_access_token");
    const refresh = localStorage.getItem("primeflix_refresh_token");
    const storedUser = localStorage.getItem("primeflix_user");

    if (token) {
      setAccessToken(token);
      setRefreshToken(refresh);
      if (storedUser) {
        try {
          setUser(JSON.parse(storedUser));
        } catch {
          // parse error fallback
        }
      }
      // Verify token freshness with backend
      fetch(`${API_BASE_URL}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((res) => {
          if (res.ok) return res.json();
          throw new Error("Unauthorized");
        })
        .then((userData) => {
          setUser(userData);
          localStorage.setItem("primeflix_user", JSON.stringify(userData));
        })
        .catch(() => {
          // Token invalid/expired
          logout();
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = (newAccess: string, newRefresh: string, newUser: User) => {
    setAccessToken(newAccess);
    setRefreshToken(newRefresh);
    setUser(newUser);
    localStorage.setItem("primeflix_access_token", newAccess);
    localStorage.setItem("primeflix_refresh_token", newRefresh);
    localStorage.setItem("primeflix_user", JSON.stringify(newUser));
  };

  const logout = () => {
    setAccessToken(null);
    setRefreshToken(null);
    setUser(null);
    localStorage.removeItem("primeflix_access_token");
    localStorage.removeItem("primeflix_refresh_token");
    localStorage.removeItem("primeflix_user");
  };

  const updateUser = (updatedUser: User) => {
    setUser(updatedUser);
    localStorage.setItem("primeflix_user", JSON.stringify(updatedUser));
  };

  return (
    <AuthContext.Provider
      value={{ user, accessToken, refreshToken, loading, login, logout, updateUser }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
