import { LoginForm } from "@components/LoginForm";

export default function Login() {
  return (
    <div className="flex items-center justify-center h-screen bg-custom-bg">
      <div className="flex flex-col w-full max-w-sm items-start justify-center bg-background p-6 rounded-sm border shadow-shadow-primary">
        <div className="text-start text-3xl font-bold py-2">C2AI</div>
        <div className="w-full mt-4">
          <LoginForm />
        </div>
      </div>
    </div>
  );
}
