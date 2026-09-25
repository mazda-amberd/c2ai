export type WhoAmIResponse = {
  identifier: string;
  service: string;
  first_name?: string | null;
  last_name?: string | null;
  metadata: {
    role: string;
    user_type: string;
    needs_password_reset: boolean;
  };
};
