"use client";

import { Eye, EyeOff, Loader } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Label } from "@ui/label";
import { Input } from "@ui/input";
import { Button } from "@ui/button";
import { useAuth } from "@auth/AuthContext";
import { useNavigate } from "react-router-dom";

interface FormValues {
  username: string;
  password: string;
}

export function LoginForm() {
  const { login } = useAuth();
  const [errorState, setErrorState] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const {
    register,
    handleSubmit,
    formState: { errors, touchedFields },
  } = useForm<FormValues>({
    defaultValues: {
      username: "",
      password: "",
    },
  });

  const onSubmit = async (data: FormValues) => {
    setLoading(true);
    setErrorState("");

    const success = await login(data.username, data.password);

    if (!success) {
      setErrorState("Username or password is incorrect");
      setLoading(false);
      return;
    }

    navigate("/");
    setLoading(false);
  };

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      className="flex flex-col gap-4 w-full mx-auto"
    >
      <h1 className="font-bold text-xl text-start">Log in to your account</h1>

      {errorState && (
        <div className="text-sm text-destructive text-start mb-2">
          {errorState}
        </div>
      )}

      <div className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="username">Username</Label>
          <Input
            id="username"
            autoFocus
            placeholder="Username"
            {...register("username", { required: "Username is required" })}
            className={
              touchedFields.username && errors.username
                ? "border-destructive"
                : ""
            }
          />
          {touchedFields.username && errors.username && (
            <p className="text-sm text-destructive">
              {errors.username.message}
            </p>
          )}
        </div>

        <div className="grid gap-2">
          <Label htmlFor="password">Password</Label>
          <div className="relative">
            <Input
              id="password"
              placeholder="Password"
              type={showPassword ? "text" : "password"}
              {...register("password", { required: "Password is required" })}
              className={
                touchedFields.password && errors.password
                  ? "border-destructive"
                  : ""
              }
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="absolute right-2 top-1/2 -translate-y-1/2 w-6 h-6 text-primary"
              onClick={() => setShowPassword(!showPassword)}
            >
              {showPassword ? (
                <EyeOff className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </Button>
          </div>
          {touchedFields.password && errors.password && (
            <p className="text-sm text-destructive">
              {errors.password.message}
            </p>
          )}
        </div>

        <Button
          type="submit"
          className="w-full"
          size="lg"
          disabled={loading}
          variant={"gradient"}
        >
          {loading ? <Loader className="animate-spin" /> : "Sign in"}
        </Button>
      </div>
    </form>
  );
}
