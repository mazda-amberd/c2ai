export type WhoAmIResponse = {
  identifier: string;
  service: string;
  metadata: {
    role: string;
    user_type: string;
    needs_password_reset: boolean;
  };
};
