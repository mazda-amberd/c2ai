"use client";

import { Eye, EyeOff, Loader } from "lucide-react";
import { forwardRef, useState, type ComponentProps, type FormEvent } from "react";
import { useForm } from "react-hook-form";
import { Label } from "@ui/label";
import { Input } from "@ui/input";
import { Button } from "@ui/button";
import { useAuth } from "@auth/AuthContext";
import { useNavigate } from "react-router-dom";
import { choosePasswordRequest, forgotPasswordRequest } from "@/api/services/auth";

interface FormValues {
  username: string;
  password: string;
}

const LINK_CLASS = "mx-auto text-sm text-text-primary hover:underline";

const PasswordInput = forwardRef<HTMLInputElement, ComponentProps<typeof Input>>(
  function PasswordInput(props, ref) {
    const [shown, setShown] = useState(false);
    return (
      <div className="relative">
        <Input ref={ref} {...props} type={shown ? "text" : "password"} />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={shown ? "Hide password" : "Show password"}
          className="absolute right-2 top-1/2 -translate-y-1/2 w-6 h-6 text-primary"
          onClick={() => setShown(!shown)}
        >
          {shown ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </Button>
      </div>
    );
  },
);

/** One card, three states, as in Amberd Agents: sign in; "Forgot password?"
 *  (emails a temporary password); and choosing your own after signing in
 *  with a temporary one. */
export function LoginForm() {
  const { login, user, mustChoosePassword, refreshUser } = useAuth();
  const [errorState, setErrorState] = useState("");
  const [note, setNote] = useState("");
  const [forgotten, setForgotten] = useState(false);
  const [resetEmail, setResetEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const {
    register,
    handleSubmit,
    getValues,
    setValue,
    setFocus,
    formState: { errors, touchedFields },
  } = useForm<FormValues>({
    defaultValues: {
      username: "",
      password: "",
    },
  });

  const clearMessages = () => {
    setErrorState("");
    setNote("");
  };

  const onSubmit = async (data: FormValues) => {
    setLoading(true);
    setErrorState("");

    const me = await login(data.username, data.password);

    if (!me) {
      setErrorState("Username or password is incorrect");
      setLoading(false);
      return;
    }

    setLoading(false);
    // A temporary password: the card asks for a new one instead.
    if (me.metadata?.needs_password_reset) return;
    navigate("/");
  };

  const openForgotten = () => {
    clearMessages();
    setResetEmail(getValues("username").trim());
    setForgotten(true);
  };

  const backToSignIn = () => {
    setErrorState("");
    setForgotten(false);
  };

  const sendReset = async (event: FormEvent) => {
    event.preventDefault();
    const email = resetEmail.trim();
    if (!email) {
      setErrorState("Enter the address you sign in with.");
      return;
    }
    setLoading(true);
    try {
      // The same answer either way - the server does not say whether the
      // address is here, and neither does this.
      const answer = await forgotPasswordRequest(email);
      backToSignIn();
      setNote(answer.detail);
      setValue("username", email);
      setTimeout(() => setFocus("password"), 60);
    } catch (error) {
      setErrorState(error instanceof Error ? error.message : "Could not send it");
    } finally {
      setLoading(false);
    }
  };

  const choosePassword = async (event: FormEvent) => {
    event.preventDefault();
    if (newPassword !== confirmPassword) {
      setErrorState("The two passwords are not the same.");
      return;
    }
    setLoading(true);
    setErrorState("");
    try {
      await choosePasswordRequest(newPassword);
      await refreshUser();
      navigate("/");
    } catch (error) {
      setErrorState(error instanceof Error ? error.message : "Could not set the password");
    } finally {
      setLoading(false);
    }
  };

  const messages = (
    <>
      {errorState && (
        <div role="alert" className="text-sm text-destructive text-start mb-2">
          {errorState}
        </div>
      )}
      {note && (
        <div
          role="status"
          className="rounded-esm border border-border-secondary bg-button-secondary px-3 py-2 text-sm text-text-secondary"
        >
          {note}
        </div>
      )}
    </>
  );

  if (mustChoosePassword) {
    const name = user?.first_name?.trim();
    return (
      <form onSubmit={choosePassword} className="flex flex-col gap-4 w-full mx-auto">
        <h1 className="font-bold text-xl text-start">Choose a password</h1>
        {messages}
        <p className="text-sm text-muted-foreground">
          {`Welcome${name ? `, ${name}` : ""}. The password you were sent is temporary — choose your own to continue.`}
        </p>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="new-password">New password</Label>
            <PasswordInput
              id="new-password"
              autoFocus
              autoComplete="new-password"
              placeholder="At least 8 characters"
              value={newPassword}
              onChange={(event) => {
                clearMessages();
                setNewPassword(event.target.value);
              }}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="confirm-password">Confirm new password</Label>
            <PasswordInput
              id="confirm-password"
              autoComplete="new-password"
              placeholder="The same again"
              value={confirmPassword}
              onChange={(event) => {
                clearMessages();
                setConfirmPassword(event.target.value);
              }}
            />
          </div>
          <Button type="submit" className="w-full" size="lg" disabled={loading} variant={"gradient"}>
            {loading ? <Loader className="animate-spin" /> : "Set password and continue"}
          </Button>
        </div>
      </form>
    );
  }

  if (forgotten) {
    return (
      <form onSubmit={sendReset} className="flex flex-col gap-4 w-full mx-auto">
        <h1 className="font-bold text-xl text-start">Reset your password</h1>
        {messages}
        <p className="text-sm text-muted-foreground">
          Enter the address you sign in with. If it belongs to an account here, a
          temporary password is emailed to it.
        </p>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="reset-email">Email</Label>
            <Input
              id="reset-email"
              type="email"
              autoFocus
              autoComplete="username"
              placeholder="you@company.com"
              value={resetEmail}
              onChange={(event) => {
                clearMessages();
                setResetEmail(event.target.value);
              }}
            />
          </div>
          <Button type="submit" className="w-full" size="lg" disabled={loading} variant={"gradient"}>
            {loading ? <Loader className="animate-spin" /> : "Email me a temporary password"}
          </Button>
        </div>
        <button type="button" className={LINK_CLASS} onClick={backToSignIn}>
          Back to sign in
        </button>
      </form>
    );
  }

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      className="flex flex-col gap-4 w-full mx-auto"
    >
      <h1 className="font-bold text-xl text-start">Log in to your account</h1>

      {messages}

      <div className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="username">Username</Label>
          <Input
            id="username"
            autoFocus
            placeholder="Username"
            {...register("username", {
              required: "Username is required",
              onChange: clearMessages,
            })}
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
          <PasswordInput
            id="password"
            placeholder="Password"
            {...register("password", {
              required: "Password is required",
              onChange: clearMessages,
            })}
            className={
              touchedFields.password && errors.password
                ? "border-destructive"
                : ""
            }
          />
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

      <button type="button" className={LINK_CLASS} onClick={openForgotten}>
        Forgot password?
      </button>
    </form>
  );
}
