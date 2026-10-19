package com.example.sms.security;
import com.example.sms.entity.AppUser;
import com.example.sms.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.CommandLineRunner;
import org.springframework.context.annotation.*;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.userdetails.*;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.csrf.CookieCsrfTokenRepository;

@Configuration @RequiredArgsConstructor
public class SecurityConfig {
    private final UserRepository userRepo;

    @Bean
    public PasswordEncoder passwordEncoder() { return new BCryptPasswordEncoder(); }

    @Bean
    public UserDetailsService userDetailsService() {
        return username -> {
            AppUser u = userRepo.findByUsername(username)
                .orElseThrow(() -> new UsernameNotFoundException(username));
            return User.withUsername(u.getUsername()).password(u.getPassword()).roles(u.getRole()).build();
        };
    }

    @Bean
    public SecurityFilterChain filter(HttpSecurity http) throws Exception {
        http
          .authorizeHttpRequests(a -> a
            .requestMatchers("/login","/css/**","/js/**","/webjars/**","/error/**").permitAll()
            .requestMatchers("/api/**").authenticated()
            .anyRequest().authenticated())
          .formLogin(f -> f.loginPage("/login").defaultSuccessUrl("/dashboard", true).permitAll())
          .logout(l -> l.logoutSuccessUrl("/login?logout").permitAll())
          .csrf(c -> c
            .csrfTokenRepository(CookieCsrfTokenRepository.withHttpOnlyFalse())
                        .ignoringRequestMatchers(request -> {
                                String path = request.getServletPath();
                                return path.equals("/api") || path.startsWith("/api/");
                        }))
          .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED));
        return http.build();
    }

    @Bean
    public CommandLineRunner seedAdmin(UserRepository repo, PasswordEncoder pe) {
        return args -> {
            if (repo.findByUsername("admin").isEmpty()) {
                repo.save(AppUser.builder().username("admin")
                    .password(pe.encode("admin123")).role("ADMIN").build());
            }
        };
    }
}
